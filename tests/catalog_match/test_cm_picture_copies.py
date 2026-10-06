"""catalog_match.retrieve: the same picture under other URLs (reader_queue, annotate_copies, SIZE_CORROBORATED).

The pool dedupes on the image URL only; after the download, near-copies (pHash distance <= 6, colours alike) are
grouped so the reader reads one copy of each picture first. Evidence only: a copy never inherits a reading, and the
domains that show a picture never change a tier, a rank or a decision. Real pHashes of generated pictures; the
reader double supplies what a model would read and verify.make_verdict decides. Sockets are blocked.
"""

import io
import random

import pytest
from PIL import Image, ImageDraw

from catalog_match import decide, facade, pipeline, retrieve
from catalog_match.fetch import phash_distance, phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, CandidateScore, FetchedImage, RankedCandidate, VlmImageVerdict
from catalog_match.score import score_candidate

from test_cm_expand import (  # noqa: F401  (the autouse fixture keeps every test offline)
    MAPPINGS, READ_EMBORG, READ_MATCH, StubFetcher, StubProvider, StubVerifier, _offline,
)

SPEC = build_sku_spec({"name": "BARTS TRADITIONAL FRIES 1KG", "brand": "BARTS"}, MAPPINGS)
READ_UNSURE_SIZE = dict(READ_MATCH, size_text="", size_match="unsure")
PALETTES = {"warm": [(200, 40, 30), (230, 120, 20), (180, 30, 90)],
            "cool": [(30, 60, 200), (20, 150, 190), (60, 30, 170)]}


def picture(seed, size=800, palette="warm"):
    """A packshot-like picture: a random layout of coloured blocks on white (the seed fixes the layout)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    colours = PALETTES[palette]
    for _ in range(14):
        x0, y0 = rnd.randint(0, 90), rnd.randint(0, 90)
        x1, y1 = min(100, x0 + rnd.randint(8, 40)), min(100, y0 + rnd.randint(8, 40))
        d.rectangle([x0 * size // 100, y0 * size // 100, x1 * size // 100, y1 * size // 100],
                    fill=colours[rnd.randrange(len(colours))])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def ph(body):
    return phash_hex(Image.open(io.BytesIO(body)))


def listing(url, host, n, title="Barts Traditional Fries 1kg"):
    return Candidate(image_url=url, page_url=f"https://www.{host}/p/{n}", title=title, page_title=title,
                     provider="serper", query_id="Q1", rank=n)


def rc_of(cand, body, verdict=None):
    with Image.open(io.BytesIO(body)) as im:
        w, h = im.size
    fetched = FetchedImage(candidate=cand, ok=True, content_sha256=f"{abs(hash(body)):064x}"[:64], width=w,
                           height=h, path_or_bytes=body, phash=ph(body))
    return RankedCandidate(candidate=cand, score=score_candidate(SPEC, cand), fetched=fetched, verdict=verdict)


def verdict(decision):
    return VlmImageVerdict(index=0, decision=decision)


def test_the_generated_pictures_behave_like_copies_and_distinct_pictures():
    a, a_small, b = picture(1), picture(1, size=500), picture(2)
    assert phash_distance(ph(a), ph(a_small)) <= retrieve.PHASH_COPY_DISTANCE
    assert phash_distance(ph(a), ph(b)) > retrieve.PHASH_COPY_DISTANCE
    assert phash_distance(ph(picture(1, palette="cool")), ph(a)) <= retrieve.PHASH_COPY_DISTANCE


# ---------------------------------------------------------------------------
# Groups and the reader's order
# ---------------------------------------------------------------------------

def test_copies_wait_behind_other_pictures_and_the_best_copy_is_read():
    small = rc_of(listing("https://img.example-cdn.com/a-small.jpg", "noon.com", 1), picture(1, size=500))
    big = rc_of(listing("https://img.example-cdn.com/a-big.jpg", "carrefouruae.com", 2), picture(1, size=1000))
    same = rc_of(listing("https://img.example-cdn.com/a-again.jpg", "carrefouruae.com", 3), picture(1, size=800))
    others = [rc_of(listing(f"https://img.example-cdn.com/o{i}.jpg", "luluhypermarket.com", 4 + i), picture(10 + i))
              for i in range(3)]
    queue = retrieve.reader_queue([small, big, same] + others)
    # the larger copy is read, at the group's best rank; the two other copies go after every other picture
    assert queue[:4] == [big] + others
    assert queue[4:] == [small, same]
    assert small.copy_of == big.candidate.image_url and same.copy_of == big.candidate.image_url
    # the domains showing the picture, each once (two copies on Carrefour count once)
    assert big.same_picture_domains == ["carrefouruae.com", "noon.com"] == small.same_picture_domains
    assert others[0].same_picture_domains == ["luluhypermarket.com"]


def test_copies_fill_a_batch_that_has_room():
    a = rc_of(listing("https://img.example-cdn.com/a1.jpg", "noon.com", 1), picture(1))
    a2 = rc_of(listing("https://img.example-cdn.com/a2.jpg", "carrefouruae.com", 2), picture(1, size=700))
    b = rc_of(listing("https://img.example-cdn.com/b.jpg", "noon.com", 3), picture(2))
    assert retrieve.reader_queue([a, a2, b]) == [a, b, a2]


def test_a_copy_whose_listing_proves_more_is_read_in_its_own_right():
    rep = rc_of(listing("https://img.example-cdn.com/a1.jpg", "carrefouruae.com", 1), picture(1))
    weaker = rc_of(listing("https://img.example-cdn.com/a2.jpg", "carrefouruae.com", 2, title="Barts Fries"),
                   picture(1, size=700))
    better_cover = RankedCandidate(candidate=weaker.candidate, fetched=weaker.fetched, score=CandidateScore(
        tier=2, matched={"brand": True, "coverage": 1.5, "source_trust": 0}))
    assert retrieve.reader_queue([rep, weaker])[1] is weaker and weaker.copy_of == rep.candidate.image_url
    assert retrieve.reader_queue([rep, better_cover]) == [rep, better_cover]
    assert better_cover.copy_of is None


def test_after_a_match_the_copies_are_not_read_after_anything_else_they_are():
    a = rc_of(listing("https://img.example-cdn.com/a1.jpg", "carrefouruae.com", 1), picture(1))
    a2 = rc_of(listing("https://img.example-cdn.com/a2.jpg", "noon.com", 2), picture(1, size=700))
    b = rc_of(listing("https://img.example-cdn.com/b.jpg", "noon.com", 3), picture(2))
    a.verdict = verdict(decide.MATCH)
    assert retrieve.reader_queue([a, a2, b]) == [a, b]                          # the picture is found
    for reading in (decide.MISMATCH, decide.UNSURE, decide.UNKNOWN):
        # a pHash cannot see a printed size or a sub-line: the reader's 'no' on one copy never speaks for another
        a.verdict = verdict(reading)
        assert retrieve.reader_queue([a, a2, b]) == [a, a2, b]
        assert a2.copy_of == a.candidate.image_url                    # still the same picture, for the reviewer
    a2.verdict = verdict(decide.MATCH)
    retrieve.reader_queue([a, a2, b])
    assert a2.copy_of is None                                          # read on its own: no copy any more


def test_a_colour_variant_is_another_picture():
    """A grey-scale pHash cannot tell a red label from a blue one: the colours keep them apart."""
    red = rc_of(listing("https://img.example-cdn.com/red.jpg", "noon.com", 1), picture(1))
    blue = rc_of(listing("https://img.example-cdn.com/blue.jpg", "noon.com", 2), picture(1, palette="cool"))
    assert [len(g) for g in retrieve.picture_groups([red, blue])] == [1, 1]
    assert retrieve.reader_queue([red, blue]) == [red, blue] and blue.copy_of is None


def test_rejected_and_failed_candidates_are_never_grouped():
    a = rc_of(listing("https://img.example-cdn.com/a1.jpg", "noon.com", 1), picture(1))
    hard = rc_of(listing("https://img.example-cdn.com/a2.jpg", "noon.com", 2, title="Barts Traditional Fries 2.5kg"),
                 picture(1, size=700))
    assert hard.score.hard_reject
    failed = rc_of(listing("https://img.example-cdn.com/a3.jpg", "noon.com", 3), picture(1, size=600))
    failed.fetched = FetchedImage(candidate=failed.candidate, ok=False, error="http_404")
    assert retrieve.picture_groups([a, hard, failed]) == [[a]]


# ---------------------------------------------------------------------------
# Through the pipeline
# ---------------------------------------------------------------------------

def run(cands, bodies, readings):
    fetcher, verifier = StubFetcher(bodies), StubVerifier(readings)
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", cands)], fetcher=fetcher,
                                          verifier=verifier, expansion=False)
    return outcome, verifier


def test_three_copies_of_a_wrong_picture_no_longer_cost_a_second_reader_call():
    """Before: the first call read the three copies of the wrong picture and one other, found no match, and a second
    call was needed for the right picture ranked sixth. Now the first call reads four different pictures."""
    wrong = [listing(f"https://img.example-cdn.com/w{i}.jpg", host, i + 1)
             for i, host in enumerate(("carrefouruae.com", "noon.com", "luluhypermarket.com"))]
    other = [listing(f"https://img.example-cdn.com/x{i}.jpg", "talabat.com", 4 + i) for i in range(2)]
    right = listing("https://img.example-cdn.com/right.jpg", "spinneys.com", 6)
    bodies = {c.image_url: picture(1, size=size) for c, size in zip(wrong, (900, 800, 700))}
    bodies.update({c.image_url: picture(20 + i) for i, c in enumerate(other)})
    bodies[right.image_url] = picture(30)
    readings = {c.image_url: READ_EMBORG for c in wrong}
    readings.update({c.image_url: READ_UNSURE_SIZE for c in other})
    readings[right.image_url] = READ_MATCH
    outcome, verifier = run(wrong + other + [right], bodies, readings)
    assert len(verifier.calls) == 1 and len(verifier.calls[0]) == 4
    read = verifier.calls[0]
    assert sum(u in read for u in (c.image_url for c in wrong)) == 1 and right.image_url in read
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == right.image_url
    unread = [rc for rc in outcome.ranked if rc.candidate in wrong and rc.verdict is None]
    rep = next(u for u in read if u in {c.image_url for c in wrong})
    assert len(unread) == 2 and all(rc.copy_of == rep for rc in unread)
    assert all(facade.evidence(rc, SPEC)["copy_of"] == rep for rc in unread)


def test_the_domains_of_a_picture_are_evidence_only():
    """Three stores show the winning picture: the reviewer sees it, but the winner, the decision and the lane are the
    ones the reader's MATCH alone gives (the same as with one store)."""
    copies = [listing(f"https://img.example-cdn.com/c{i}.jpg", host, i + 1)
              for i, host in enumerate(("carrefouruae.com", "noon.com", "luluhypermarket.com"))]
    bodies = {c.image_url: picture(1, size=s) for c, s in zip(copies, (900, 800, 700))}
    outcome, _ = run(copies, bodies, {c.image_url: READ_MATCH for c in copies})
    alone, _ = run(copies[:1], {copies[0].image_url: bodies[copies[0].image_url]},
                   {copies[0].image_url: READ_MATCH})
    assert outcome.winner.candidate.image_url == alone.winner.candidate.image_url == copies[0].image_url
    assert outcome.decision == alone.decision
    assert decide.lane_of(outcome.winner.reasons) == decide.lane_of(alone.winner.reasons)
    assert outcome.winner.same_picture_domains == ["carrefouruae.com", "luluhypermarket.com", "noon.com"]
    ev = facade.evidence(outcome.winner, SPEC)
    assert ev["same_picture_domains"] == ["carrefouruae.com", "luluhypermarket.com", "noon.com"]
    assert ev["consensus_count"] == 1                                  # the URL-level count is unchanged


# ---------------------------------------------------------------------------
# SIZE_CORROBORATED (record only)
# ---------------------------------------------------------------------------

def _size_case(hosts, reading, titles=None):
    titles = titles or ["Barts Traditional Fries 1kg"] * len(hosts)
    cands = [listing(f"https://img.example-cdn.com/s{i}.jpg", host, i + 1, title=t)
             for i, (host, t) in enumerate(zip(hosts, titles))]
    bodies = {c.image_url: picture(5, size=900 - 100 * i) for i, c in enumerate(cands)}
    return run(cands, bodies, {cands[0].image_url: reading})


def test_size_corroborated_when_two_trusted_stores_state_the_size_the_label_left_open():
    outcome, _ = _size_case(("carrefouruae.com", "luluhypermarket.com"), READ_UNSURE_SIZE)
    first = next(rc for rc in outcome.ranked if rc.verdict is not None)
    assert first.verdict.size_match == "unsure" and retrieve.SIZE_CORROBORATED in first.reasons
    # record only: the decision and the lane are what they are without it
    without = [r for r in first.reasons if r != retrieve.SIZE_CORROBORATED]
    assert decide.lane_of(first.reasons) == decide.lane_of(without)
    assert outcome.decision != "AUTO_PUBLISH" and "auto_publish" not in first.reasons


@pytest.mark.parametrize("hosts, reading, titles", [
    (("carrefouruae.com", "carrefouruae.com"), READ_UNSURE_SIZE, None),            # one store, twice
    (("carrefouruae.com", "someblog.example.com"), READ_UNSURE_SIZE, None),        # one untrusted page
    (("carrefouruae.com", "luluhypermarket.com"), READ_MATCH, None),               # the label read the size
    (("carrefouruae.com", "luluhypermarket.com"), dict(READ_UNSURE_SIZE, variant_match="no"), None),
    (("carrefouruae.com", "luluhypermarket.com"), dict(READ_UNSURE_SIZE, brand_match="unsure"), None),
    (("carrefouruae.com", "luluhypermarket.com"), READ_UNSURE_SIZE, ["Barts Traditional Fries"] * 2),  # no size
])
def test_no_size_corroborated_without_every_condition(hosts, reading, titles):
    outcome, _ = _size_case(hosts, reading, titles)
    assert not any(retrieve.SIZE_CORROBORATED in rc.reasons for rc in outcome.ranked)
