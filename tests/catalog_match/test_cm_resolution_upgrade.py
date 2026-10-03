"""Best-resolution copy (decide.resolution_upgrade): publish the largest copy of the winning picture.

Most correct picks of the 2026-09-30 dry run came from UAE retailer pages, and the same packshot is
often on several of them at different sizes (Lulu 500 px, Carrefour 1200 px). Once the winner and the
decision are fixed, a fetched copy of the same picture (pHash distance <= 6, aspect within 10 %) with a
larger short side is published instead, keeping the winner's verdict and decision. Identity comes
before image quality: the copy's own listing must be no weaker, and it may add no risk.
"""

import socket

import pytest

from catalog_match import decide, facade, pipeline, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult, VlmImageVerdict,
)
from catalog_match.score import score_candidate

from test_cm_pipeline import StubFetcher, StubProvider, StubVerifier, packshot_png, phash_of

SPEC = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "category": "Dairy"},
                      {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "official_domains": ["almarai.com"],
                                   "excluded_competitors": ["Al Ain"]}})
HEALTHY = [ProviderHealth("serper", "ok", 200, query_id="Q1")]
OK = VerificationResult(status="ok", calls=1)
DOWN = VerificationResult(status="unknown", calls=1, error="http_429")
PHASH = "f0f0f0f0f0f0f0f0"
NEAR = "f0f0f0f0f0f0f0f3"        # distance 2
EDGE = "f0f0f0f0f0f0f03f"        # distance 6
FAR = "f0f0f0f0f0f0f13f"         # distance 7: just outside the limit

LULU = dict(image_url="https://www.luluhypermarket.com/medias/almarai-full-fat-milk-1l-500.jpg",
            page_url="https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/2",
            title="Almarai Full Fat Milk 1L Online at Best Price | Lulu UAE")
CARREFOUR = dict(image_url="https://cdn.mafrservices.com/sys-master-root/h1/108596_main.jpg",
                 page_url="https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk-1l/p/108596",
                 title="Buy Almarai Full Fat Milk 1L Online - Shop on Carrefour UAE")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("GTIN_POLICY", raising=False)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def reading(decision="MATCH", **over):
    base = dict(brand_text="Almarai", variant_text="Full Fat Milk", size_text="1 L", view="front_packshot",
                brand_match="yes", variant_match="yes", size_match="yes")
    base.update(over)
    return VlmImageVerdict(index=0, decision=decision, **base)


def cand(where, rank=1, **over):
    fields = dict(where, provider="serper", query_id="Q1", rank=rank)
    fields["page_title"] = fields["title"]
    fields.update(over)
    return Candidate(**fields)


def rc(c, size, phash=PHASH, verdict=None, quality_ok=True, sha="ab"):
    fi = FetchedImage(candidate=c, ok=True, content_sha256=sha * 32, width=size[0], height=size[1],
                      path_or_bytes=b"x", phash=phash)
    return RankedCandidate(candidate=c, score=score_candidate(SPEC, c), fetched=fi,
                           quality=QualityReport(hard_ok=quality_ok, quality_score=0.5), verdict=verdict)


def route(rcs, verification=OK, relaxed=()):
    return decide.route(SPEC, rcs, verification, HEALTHY, set(relaxed))


def pair(copy_size=(1200, 1200), copy_phash=NEAR, copy_verdict=None, **copy_over):
    """The 500 px Lulu winner (MATCH) and a larger Carrefour copy of the same picture."""
    winner = rc(cand(LULU, rank=1), (500, 500), verdict=reading())
    copy = rc(cand(CARREFOUR, rank=2, **copy_over), copy_size, phash=copy_phash, verdict=copy_verdict, sha="cd")
    return winner, copy


# ---------------------------------------------------------------------------
# The upgrade
# ---------------------------------------------------------------------------

def test_a_larger_copy_of_the_same_picture_is_published_with_the_winners_verdict():
    winner, copy = pair()
    out = route([winner, copy])
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.winner is copy and copy.status == "preselected" and winner.status == "eligible"
    assert "preselected:vlm_match" in copy.reasons and "resolution_upgrade:500x1200" in copy.reasons
    assert "resolution_upgrade:replaced" in winner.reasons
    assert not any(r.startswith("preselected:") for r in winner.reasons)
    # the verdict is not copied onto an image the verifier did not see; the decision rests on the winner's
    assert copy.verdict is None and winner.verdict.decision == "MATCH"
    # the 500 px winner would have said nothing about resolution; the larger copy carries no warning
    assert decide.warning_codes(copy.reasons) == []
    assert [r.status for r in out.ranked].count("preselected") == 1
    legacy = facade.outcome_to_legacy(out)
    assert legacy["url"] == CARREFOUR["image_url"] and (legacy["width"], legacy["height"]) == (1200, 1200)
    assert legacy["content_sha256"] == "cd" * 32 and legacy["page_url"] == CARREFOUR["page_url"]
    assert legacy["decision"] == "REVIEW_PRESELECTED" and legacy["preselect"] is True


def test_the_largest_qualifying_copy_wins_and_route_is_idempotent():
    winner, copy = pair(copy_size=(900, 900))
    bigger = rc(cand(dict(CARREFOUR, image_url="https://cdn.mafrservices.com/sys-master-root/h1/108596_zoom.jpg"),
                     rank=3), (1500, 1500), phash=EDGE, sha="ef")
    out = route([winner, copy, bigger])
    assert out.winner is bigger and "resolution_upgrade:500x1500" in bigger.reasons
    snapshot = [(r.candidate.image_url, r.status, list(r.reasons)) for r in out.ranked]
    again = route(out.ranked)
    assert again.winner is bigger
    assert [(r.candidate.image_url, r.status, list(r.reasons)) for r in again.ranked] == snapshot


def test_a_low_resolution_warning_goes_away_with_the_larger_copy():
    winner, copy = pair()
    alone = rc(cand(LULU), (400, 400), verdict=reading())
    assert "warn:low_resolution" in route([alone]).winner.reasons
    winner.fetched.width = winner.fetched.height = 400
    out = route([winner, copy])
    assert out.winner is copy and "resolution_upgrade:400x1200" in copy.reasons
    assert "warn:low_resolution" not in copy.reasons


def test_a_copy_with_its_own_match_keeps_it():
    winner, copy = pair(copy_verdict=reading())
    out = route([winner, copy])
    assert out.winner is copy and copy.verdict.decision == "MATCH"


def test_the_tier1_fallback_winner_is_upgraded_too():
    # verifier down for the whole SKU: a tier-1 UNKNOWN winner may be pre-checked; its larger copy too
    winner = rc(cand(LULU, rank=1), (500, 500), verdict=VlmImageVerdict(index=0))
    copy = rc(cand(CARREFOUR, rank=2), (1200, 1200), phash=NEAR, sha="cd")
    out = route([winner, copy], verification=DOWN)
    assert out.winner is copy and out.failure_code == "VERIFIER_DOWN"
    assert "preselected:tier1_unknown" in copy.reasons and "warn:vlm_unsure" in copy.reasons


# ---------------------------------------------------------------------------
# When it must not happen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label, kw", [
    ("far_phash", dict(copy_phash=FAR)),
    ("no_phash", dict(copy_phash=None)),
    ("aspect", dict(copy_size=(1200, 1400))),                 # 0.857 vs 1.0: more than 10 % apart
    ("smaller", dict(copy_size=(480, 480))),
    ("same_size", dict(copy_size=(500, 500))),
    ("copy_mismatch", dict(copy_verdict=reading("MISMATCH", variant_match="no", variant_text="Low Fat"))),
    ("copy_unsure", dict(copy_verdict=reading("UNSURE", view="other_side"))),
    ("unsanctioned", dict(sanctioned=False)),
    ("relaxed", dict(query_id="R2")),
    ("lower_trust", dict(image_url="https://www.grocerysite.example/almarai.jpg",
                         page_url="https://www.grocerysite.example/almarai-full-fat-milk-1l")),
    ("lower_tier", dict(title="Almarai Milk 1L", page_title="Almarai Milk 1L",
                        page_url="https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-milk-1l/p/1")),
    ("variant_conflict", dict(title="Almarai Low Fat Milk 1L", page_title="Almarai Low Fat Milk 1L",
                              page_url="https://www.carrefouruae.com/mafuae/en/almarai-low-fat-milk-1l/p/1")),
    ("foreign_store", dict(page_url="https://www.carrefourksa.com/mafsau/en/almarai-full-fat-milk-1l/p/1",
                           image_url="https://cdn.mafrservices.com/ksa/108596.jpg")),
    ("barcode_conflict", dict(gtin_on_page="4006381333931")),
    # same page trust and tier, but the copy itself would add a reviewer warning
    ("social_image", dict(image_url="https://scontent.xx.fbcdn.net/v/almarai-full-fat-milk-1l.jpg")),
    ("chat_export", dict(image_url="https://cdn.mafrservices.com/WhatsApp%20Image%202025-10-14%20at%2010.07.41.jpeg")),
])
def test_no_upgrade(label, kw):
    over = {k: v for k, v in kw.items() if k not in ("copy_size", "copy_phash", "copy_verdict")}
    args = {k: v for k, v in kw.items() if k in ("copy_size", "copy_phash", "copy_verdict")}
    spec_gtin = label == "barcode_conflict"
    winner, copy = pair(**args, **over)
    if label == "relaxed":
        out = route([winner, copy], relaxed={"R2"})
    elif spec_gtin:
        spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": "6281007000024"},
                              {"almarai": {"brand": "Almarai"}})
        winner.score = score_candidate(spec, winner.candidate)
        copy.score = score_candidate(spec, copy.candidate)
        out = decide.route(spec, [winner, copy], OK, HEALTHY, set())
    else:
        out = route([winner, copy])
    assert out.winner is winner, label
    assert not any(r.startswith("resolution_upgrade") for r in winner.reasons + copy.reasons), label
    assert copy.status != "preselected", label


LULU_NOSIZE = dict(image_url="https://www.luluhypermarket.com/medias/almarai-full-fat-milk-500.jpg",
                   page_url="https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk/p/2",
                   title="Almarai Full Fat Milk | Lulu UAE")


@pytest.mark.parametrize("label, copy_over, conflict", [
    # the copy's own image file says another variant: a grey-scale pHash cannot tell a low-fat carton
    # from the full-fat one, and the verifier never saw this image
    ("image_file_variant", dict(image_url="https://cdn.mafrservices.com/p/almarai-low-fat-milk.jpg",
                                title="Buy Almarai Full Fat Milk - Shop on Carrefour UAE",
                                page_url="https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk/p/3"),
     "image_variant_conflict:fat"),
    # the copy's page URL says a pack of 6
    ("url_pack", dict(title="Buy Almarai Full Fat Milk - Shop on Carrefour UAE",
                      page_url="https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk-1l-pack-of-6/p/3"),
     "url_pack_conflict:page_slug"),
])
def test_no_upgrade_to_a_copy_with_a_conflict_the_winner_lacks(label, copy_over, conflict):
    # Review fix: both listings are tier 2 and the copy is no weaker on any rank key, but its own
    # evidence carries a size / pack / variant doubt the verified winner does not: identity comes
    # before resolution, so the verified 500 px picture stays.
    winner = rc(cand(LULU_NOSIZE, rank=1), (500, 500), verdict=reading())
    copy = rc(cand(dict(CARREFOUR, **copy_over), rank=2), (1200, 1200), phash=NEAR, sha="cd")
    assert winner.score.tier == 2 and winner.score.conflicts == ()
    assert copy.score.tier == 2 and conflict in copy.score.conflicts and not copy.score.hard_reject
    out = route([winner, copy])
    assert out.winner is winner, label
    assert not any(r.startswith("resolution_upgrade") for r in winner.reasons + copy.reasons), label
    # control: the same copy without the doubt is published
    clean = rc(cand(dict(CARREFOUR, title="Buy Almarai Full Fat Milk - Shop on Carrefour UAE",
                         page_url="https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk/p/3"),
                    rank=2), (1200, 1200), phash=NEAR, sha="cd")
    winner = rc(cand(LULU_NOSIZE, rank=1), (500, 500), verdict=reading())
    assert clean.score.conflicts == () and route([winner, clean]).winner is clean


GENERIC_W = dict(image_url="https://shop-one.example/img/almarai-500.jpg",
                 page_url="https://shop-one.example/almarai/p/1")
GENERIC_C = dict(image_url="https://shop-two.example/img/almarai-1200.jpg",
                 page_url="https://shop-two.example/almarai/p/2")


@pytest.mark.parametrize("label, winner_where, copy_where", [
    # both tier 2; the copy is weaker on exactly one rank key
    ("lower_trust", dict(LULU_NOSIZE), dict(GENERIC_C, title="Almarai Full Fat Milk")),
    ("weaker_size", dict(GENERIC_W, title="Almarai Full Fat Milk 1L"), dict(GENERIC_C, title="Almarai Full Fat Milk")),
    ("fewer_variants", dict(GENERIC_W, title="Almarai Full Fat Milk 1L"), dict(GENERIC_C, title="Almarai Milk 1L")),
    ("lower_coverage", dict(GENERIC_W, title="Almarai Full Fat Milk 1L"), dict(GENERIC_C, title="Almarai Full Fat 1L")),
])
def test_no_upgrade_to_a_copy_weaker_on_one_rank_key(label, winner_where, copy_where):
    winner = rc(cand(winner_where, rank=1), (500, 500), verdict=reading())
    copy = rc(cand(copy_where, rank=2), (1200, 1200), phash=NEAR, sha="cd")
    assert winner.score.tier == copy.score.tier == 2 and copy.score.conflicts == ()
    assert decide.review_warnings(SPEC, copy, reading_of=winner) == decide.review_warnings(SPEC, winner), label
    out = route([winner, copy])
    assert out.winner is winner, label


def test_the_largest_copy_wins_whatever_the_order():
    winner, copy = pair(copy_size=(900, 900))
    bigger = rc(cand(dict(CARREFOUR, image_url="https://cdn.mafrservices.com/sys-master-root/h1/108596_zoom.jpg"),
                     rank=3), (1500, 1500), phash=EDGE, sha="ef")
    out = route([winner, bigger, copy])
    assert out.winner is bigger and "resolution_upgrade:500x1500" in bigger.reasons


def test_no_upgrade_to_a_copy_whose_own_match_reads_another_size():
    # defence in depth: two MATCH readings that disagree (here a hand-built reading of '2 L')
    winner, copy = pair(copy_verdict=reading(size_text="2 L"))
    assert decide.identity_conflict(winner, copy, SPEC) == "size"
    out = route([winner, copy])
    assert out.winner is winner and copy.status != "preselected"


def test_a_copy_may_share_the_winners_own_doubt():
    # the winner was chosen with the doubt on record (verifier MATCH); a copy carrying the same
    # doubt and nothing more adds no risk
    spec = build_sku_spec({"name": "Almarai Milk 1L", "brand": "Almarai"}, {"almarai": {"brand": "Almarai"}})
    w_c = cand(dict(LULU, title="Almarai Milk 1L / 6 x 1L | Lulu UAE",
                    page_url="https://www.luluhypermarket.com/en-ae/p/2"), rank=1)
    c_c = cand(dict(CARREFOUR, title="Almarai Milk 1L / 6 x 1L - Carrefour UAE",
                    page_url="https://www.carrefouruae.com/mafuae/en/p/3"), rank=2)
    winner, copy = rc(w_c, (500, 500), verdict=reading(variant_text="Milk")), rc(c_c, (1200, 1200), phash=NEAR,
                                                                                 sha="cd")
    winner.score, copy.score = score_candidate(spec, w_c), score_candidate(spec, c_c)
    assert winner.score.conflicts == copy.score.conflicts == ("pack_ambiguous",)
    out = decide.route(spec, [winner, copy], OK, HEALTHY, set())
    assert out.winner is copy and "resolution_upgrade:500x1200" in copy.reasons


def test_no_upgrade_to_a_reviewer_negative():
    # the pipeline marks images a reviewer rejected before as 'excluded': never published again
    winner, copy = pair()
    copy.status = "excluded"
    out = route([winner, copy])
    assert out.winner is winner and copy.status == "excluded"
    # a candidate the pipeline rejected for its own reason stays out too
    winner, copy = pair()
    copy.status, copy.reasons = "rejected", ["pipeline:own_reason"]
    out = route([winner, copy])
    assert out.winner is winner and copy.status == "rejected"


def test_no_upgrade_from_a_hard_rejected_or_quality_failed_or_failed_download_copy():
    winner, copy = pair(title="Almarai Full Fat Milk 2L", page_title="Almarai Full Fat Milk 2L")
    assert copy.score.hard_reject                           # size conflict
    assert route([winner, copy]).winner is winner
    winner, copy = pair()
    copy.quality = QualityReport(hard_ok=False, hard_reasons=["watermark"])
    assert route([winner, copy]).winner is winner
    winner, copy = pair()
    copy.fetched.ok, copy.fetched.error = False, "http_403"
    assert route([winner, copy]).winner is winner


def test_resolution_upgrade_checks_quality_itself():
    # called directly (not through route, which marks a quality failure 'rejected'), the hard
    # quality gate still holds
    winner, copy = pair()
    copy.quality = QualityReport(hard_ok=False, hard_reasons=["watermark"])
    assert decide.resolution_upgrade(SPEC, winner, [winner, copy]) is None
    winner, copy = pair()
    assert decide.resolution_upgrade(SPEC, winner, [winner, copy]) is copy


def test_no_upgrade_between_two_barcode_conflicts():
    # a winner whose page barcode differs (pre-checked on a full MATCH, warned) is not swapped for
    # another conflicting page, even one with the same code
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": "6281007000024"},
                          {"almarai": {"brand": "Almarai"}})
    winner, copy = pair(gtin_on_page="4006381333931")
    winner.candidate = cand(LULU, rank=1, gtin_on_page="4006381333931")
    winner.score, copy.score = score_candidate(spec, winner.candidate), score_candidate(spec, copy.candidate)
    out = decide.route(spec, [winner, copy], OK, HEALTHY, set())
    assert out.winner is winner and "warn:barcode_conflict" in winner.reasons


def test_no_upgrade_across_tiers_downward_but_upward_is_allowed():
    weak = dict(LULU, image_url="https://www.luluhypermarket.com/medias/2.jpg", title="Almarai Milk | Lulu UAE",
                page_url="https://www.luluhypermarket.com/en-ae/almarai-milk/p/2")
    # downward: a tier-1 winner and a tier-2 copy of the same picture
    winner = rc(cand(CARREFOUR), (500, 500), verdict=reading())
    copy = rc(cand(weak, rank=2), (1200, 1200), phash=NEAR, sha="cd")
    assert winner.score.tier == 1 and copy.score.tier == 2
    assert route([winner, copy]).winner is winner
    # upward: a tier-2 winner (Gemini MATCH) and a tier-1 copy the verifier did not see
    winner = rc(cand(weak), (500, 500), verdict=reading())
    copy = rc(cand(CARREFOUR, rank=2), (1200, 1200), phash=NEAR, sha="cd")
    assert winner.score.tier == 2 and copy.score.tier == 1
    out = route([copy, winner])
    assert out.winner is copy and "resolution_upgrade:500x1200" in copy.reasons
    # an UNSURE copy never replaces a MATCH winner, whatever its tier
    winner = rc(cand(weak), (500, 500), verdict=reading())
    copy = rc(cand(CARREFOUR, rank=2), (1200, 1200), phash=NEAR, sha="cd", verdict=reading("UNSURE", view="other_side"))
    assert route([copy, winner]).winner is winner


def test_auto_publish_upgrades_only_to_a_copy_with_its_own_match(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "Almarai")
    winner, copy = pair()                                   # copy never seen by the verifier
    out = route([winner, copy])
    assert out.decision == "AUTO_PUBLISH" and out.winner is winner and "auto_publish" in winner.reasons
    winner, copy = pair(copy_verdict=reading())
    out = route([winner, copy])
    assert out.decision == "AUTO_PUBLISH" and out.winner is copy
    assert "auto_publish" in copy.reasons and "auto_publish" not in winner.reasons
    assert "resolution_upgrade:500x1200" in copy.reasons
    legacy = facade.outcome_to_legacy(out)
    assert legacy["url"] == CARREFOUR["image_url"] and legacy["needs_review"] is False


def test_auto_publish_never_upgrades_to_an_unsanctioned_copy(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "Almarai")
    winner, copy = pair(copy_verdict=reading(), sanctioned=False)
    out = route([winner, copy])
    assert out.decision == "AUTO_PUBLISH" and out.winner is winner


def test_auto_publish_never_upgrades_to_a_copy_trusted_only_through_a_learned_source(monkeypatch):
    # A site the reviewers keep approving Almarai from (catalog_match.learning) scores like a UAE retailer,
    # so its larger copy passes 'not weaker'; but what blocks a winner's auto-publish blocks a copy's too.
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "Almarai")
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "category": "Dairy"},
                          {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "official_domains": ["almarai.com"],
                                       "learned_domains": ["example-grocer.com"]}})
    grocer = dict(image_url="https://img.example-grocer.com/almarai-full-fat-milk-1l-1500.jpg",
                  page_url="https://www.example-grocer.com/almarai-full-fat-milk-1l",
                  title="Almarai Full Fat Milk 1L")

    def ranked(c, size, phash, sha):
        fi = FetchedImage(candidate=c, ok=True, content_sha256=sha * 32, width=size[0], height=size[1],
                          path_or_bytes=b"x", phash=phash)
        return RankedCandidate(candidate=c, score=score_candidate(spec, c), fetched=fi,
                               quality=QualityReport(hard_ok=True, quality_score=0.5), verdict=reading())

    winner = ranked(cand(LULU, rank=1), (500, 500), PHASH, "ab")
    copy = ranked(cand(grocer, rank=2), (1500, 1500), NEAR, "cd")
    assert copy.score.matched.get("source_class") == "reviewed_source" and copy.score.tier == 1
    out = decide.route(spec, [winner, copy], OK, HEALTHY, set())
    assert out.decision == "AUTO_PUBLISH" and out.winner is winner
    assert not any(r.startswith("resolution_upgrade") for r in winner.reasons + copy.reasons)
    # under review the larger copy is still offered: the reviewer sees where it comes from
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    winner = ranked(cand(LULU, rank=1), (500, 500), PHASH, "ab")
    copy = ranked(cand(grocer, rank=2), (1500, 1500), NEAR, "cd")
    out = decide.route(spec, [winner, copy], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is copy


# ---------------------------------------------------------------------------
# End to end through the pipeline
# ---------------------------------------------------------------------------

def test_pipeline_publishes_the_larger_copy():
    small = cand(LULU, rank=1)
    large = cand(CARREFOUR, rank=2)
    small_png, large_png = packshot_png(5, (500, 500)), packshot_png(5, (1400, 1400))
    assert phash_of(small_png) is not None
    from catalog_match.fetch import phash_distance
    assert phash_distance(phash_of(small_png), phash_of(large_png)) <= decide.RESOLUTION_PHASH_MAX
    read = {"brand_text": "Almarai", "variant_text": "Full Fat Milk", "size_text": "1 L", "view": "front_packshot",
            "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
    out = pipeline.find_product_image(
        SPEC, providers=[StubProvider("serper", [small, large])],
        fetcher=StubFetcher({small.image_url: small_png, large.image_url: large_png}),
        verifier=StubVerifier({small.image_url: read, large.image_url: read}))
    assert out.winner is not None and out.winner.candidate.image_url == large.image_url
    assert out.decision == "REVIEW_PRESELECTED"
