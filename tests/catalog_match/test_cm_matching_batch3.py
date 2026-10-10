"""Matching batch 3: a multipack's total weight is no size conflict, unioncoop.ae ranks last, and the stored
trace lists the hard-rejected candidates in short."""

import json
import socket

import pytest

from catalog_match import facade
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, SearchOutcome, VlmImageVerdict,
)
from catalog_match.score import TRUST_DEMOTED, TRUST_GENERIC, rank, score_candidate, source_trust
from catalog_match.sizes import parse_sizes, product_size, total_match
from catalog_match.verify import (MATCH, MISMATCH, UNSURE, classify, multipack_unit_image, multipack_whole_image,
                                  size_agreement, size_flag)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


MAPPINGS = {"oreo": {"brand": "Oreo", "synonyms": ["اوريو"], "excluded_competitors": ["Loacker"]}}


def spec_for(name):
    return build_sku_spec({"name": name, "brand": "Oreo"}, MAPPINGS)


def listing(title, domain="carrefouruae.com", image="https://cdn.example.com/p/1.jpg", **kw):
    return Candidate(image_url=image, title=title, page_url=f"https://www.{domain}/en/p/1", domain=domain, **kw)


def size(text):
    ps = product_size(parse_sizes(text, "name"))
    assert ps is not None and not isinstance(ps, str), (text, ps)
    return ps


# ---------------------------------------------------------------------------
# 1. pack count x unit size == total size
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target, field", [
    ("12 x 30g", "360g"),
    ("12 x 30g", "0.36 kg"),            # g / kg normalised
    ("360g", "12 x 30g"),               # the reverse
    ("6 x 1L", "6000 ml"),              # ml / L normalised
    ("12 x 30g", "12 x 30g (360g)"),    # both forms in one field
    ("2X2KG", "4 kg"),
    ("12 x 30g", "361g"),               # within the size tolerance
])
def test_total_match_reads_the_total_in_the_other_form(target, field):
    assert total_match(size(target), parse_sizes(field, "title"))


@pytest.mark.parametrize("target, field", [
    ("12 x 30g", "300g"),               # 360 g is no 300 g
    ("6 x 1L", "12 x 1L"),              # another pack of the same unit
    ("6 x 1L", "2 x 3L"),               # both sides multipacks: another product
    ("12 x 30g", "30g"),                # one unit (compare's own case, multipack_unit_image)
    ("12 x 30g", "12 x 30g"),           # the same wording is compare's match, not a total
    ("360g", "360 ml"),                 # another dimension
    ("12 x 30g", "360g 6 packs"),       # a pack stated on its own that is not the multipack's
    ("360g", "12 x 30g, 6 x 60g"),      # two different multipacks
])
def test_total_match_keeps_real_differences(target, field):
    assert not total_match(size(target), parse_sizes(field, "title"))


def test_a_multipack_listed_with_its_total_weight_is_no_size_conflict():
    sc = score_candidate(spec_for("Oreo Biscuits 12 x 30g"), listing("Oreo Biscuits 360g"))
    assert sc.hard_reject == ()
    assert sc.size_status == "match"
    assert sc.matched["pack"] == "unknown"          # the total does not prove the pack: never tier 1 on it
    assert sc.tier == 2
    assert "size_total_form:title" in sc.conflicts


def test_a_single_unit_listed_as_a_multipack_of_its_weight_is_no_conflict_and_caps_at_tier2():
    sc = score_candidate(spec_for("Oreo Biscuits 360g"), listing("Oreo Biscuits 12 x 30g"))
    assert sc.hard_reject == ()
    assert sc.size_status == "match"
    assert sc.tier == 2


def test_a_listing_stating_both_forms_is_tier1():
    sc = score_candidate(spec_for("Oreo Biscuits 12 x 30g"), listing("Oreo Biscuits 12 x 30g (360g)"))
    assert sc.hard_reject == ()
    assert sc.tier == 1


@pytest.mark.parametrize("name, title, rule", [
    ("Oreo Biscuits 12 x 30g", "Oreo Biscuits 300g", "size_conflict"),
    ("Oreo Water 6 x 1L", "Oreo Water 12 x 1L", "pack_conflict"),
    ("Oreo Water 6 x 1L", "Oreo Water 2 x 3L", "size_conflict"),
])
def test_real_size_and_pack_differences_stay_hard_rejects(name, title, rule):
    sc = score_candidate(spec_for(name), listing(title))
    assert rule in sc.hard_reject
    assert sc.tier is None


def _reading(size_text, pack_count=None, size_match="no"):
    return VlmImageVerdict(index=0, brand_text="Oreo", variant_text="Biscuits", size_text=size_text,
                           pack_count=pack_count, view="front_packshot", brand_match="yes",
                           variant_match="yes", size_match=size_match)


def test_the_label_reader_takes_the_printed_total_as_the_whole_multipack():
    spec = spec_for("Oreo Biscuits 12 x 30g")
    v = _reading("360g", pack_count=12)
    assert size_agreement(spec, "360g") == "match"
    assert multipack_whole_image(spec, v) and size_flag(spec, v) == "yes"
    assert not multipack_unit_image(spec, v)           # the total is the whole pack, never one unit
    assert classify(spec, v) == MATCH
    # the pack not read: the size is no 'no', but nothing proves the pack
    assert classify(spec, _reading("360g")) == UNSURE
    # 300 g is still another size
    assert classify(spec, _reading("300g", pack_count=12)) == MISMATCH
    assert size_agreement(spec, "300g") == "conflict"


def test_the_label_reader_keeps_one_unit_and_the_same_wording_as_before():
    spec = spec_for("Oreo Biscuits 12 x 30g")
    one = _reading("30g", pack_count=1)
    assert multipack_unit_image(spec, one) and classify(spec, one) == UNSURE
    whole = _reading("12 x 30g", pack_count=12)
    assert multipack_whole_image(spec, whole) and classify(spec, whole) == MATCH


def test_a_single_unit_sku_read_as_a_multipack_of_its_total_is_review_only():
    spec = spec_for("Oreo Biscuits 360g")
    assert classify(spec, _reading("12 x 30g", size_match="no")) == UNSURE
    assert classify(spec, _reading("12 x 30g", size_match="yes")) == UNSURE
    assert classify(spec, _reading("360g", pack_count=1, size_match="yes")) == MATCH


# ---------------------------------------------------------------------------
# 2. unioncoop.ae: the lowest source trust
# ---------------------------------------------------------------------------

def test_unioncoop_has_the_lowest_trust_and_ranks_below_a_generic_site():
    spec = spec_for("Oreo Biscuits 12 x 30g")
    union = listing("Oreo Biscuits 12 x 30g", domain="unioncoop.ae", image="https://cdn.example.com/union.jpg",
                    rank=1)
    blog = listing("Oreo Biscuits 12 x 30g", domain="blog.example.org", image="https://cdn.example.com/blog.jpg",
                   rank=2)
    level, name = source_trust(spec, union)
    assert (level, name) == (TRUST_DEMOTED, "demoted")
    assert level < source_trust(spec, blog)[0] == TRUST_GENERIC
    su, sb = score_candidate(spec, union), score_candidate(spec, blog)
    assert su.hard_reject == () and su.tier == 2 and "demoted_source" in su.conflicts
    ordered = rank([(union, su), (blog, sb)])
    assert [c.domain for c, _ in ordered] == ["blog.example.org", "unioncoop.ae"]


def test_unioncoop_stays_listed_for_search_and_fetching():
    from catalog_match.score import trusted_domains
    data = trusted_domains()
    assert "unioncoop.ae" in data["demoted"]
    assert "unioncoop.ae" in data["uae_retailers"]


def test_a_tier1_uae_retailer_is_unaffected():
    spec = spec_for("Oreo Biscuits 12 x 30g")
    sc = score_candidate(spec, listing("Oreo Biscuits 12 x 30g", domain="carrefouruae.com"))
    assert sc.tier == 1 and sc.matched["source_class"] == "uae_retailer"


# ---------------------------------------------------------------------------
# 3. hard rejections in the stored trace
# ---------------------------------------------------------------------------

SPEC = spec_for("Oreo Biscuits 12 x 30g")


def _rc(cand, status="eligible", reasons=(), fetched_ok=True, error=None):
    fi = FetchedImage(candidate=cand, ok=fetched_ok, error=error, content_sha256="ab" * 32, width=1000,
                      height=1000)
    return RankedCandidate(candidate=cand, score=score_candidate(SPEC, cand), fetched=fi,
                           quality=QualityReport(hard_ok=True, soft={}, quality_score=0.8),
                           status=status, reasons=list(reasons))


def test_the_trace_lists_each_hard_rejection_in_short():
    good = _rc(listing("Oreo Biscuits 12 x 30g", image="https://cdn.example.com/good.jpg"))
    wrong_size = _rc(listing("Oreo Biscuits 300g", image="https://cdn.example.com/a/b/oreo_300g.jpg?w=800&h=800"),
                     status="rejected", reasons=["hard:size_conflict"])
    stock = _rc(listing("Oreo Biscuits 12 x 30g stock photo", domain="shutterstock.com",
                        image="https://image.shutterstock.com/oreo-123.jpg"), status="rejected")
    not_image = _rc(listing("Oreo Biscuits 12 x 30g", domain="noon.com", image="https://f.nooncdn.com/p/x.html"),
                    status="rejected", reasons=["download:not_image"], fetched_ok=False, error="not_image")
    excluded = _rc(listing("Oreo Biscuits 300g", image="https://cdn.example.com/neg.jpg"), status="excluded")
    mismatch = _rc(listing("Oreo Biscuits 12 x 30g", image="https://cdn.example.com/mm.jpg"), status="rejected",
                   reasons=["vlm:MISMATCH"])
    outcome = SearchOutcome(decision="REVIEW_UNSELECTED", ranked=[good, wrong_size, stock, not_image, excluded,
                                                                  mismatch],
                            provider_health=[ProviderHealth("serper", "ok", 200)], sku_key=SPEC.sku_key)
    trace = {}
    facade.outcome_to_legacy(outcome, trace, SPEC)
    entries = trace["outcome"]["hard_rejected"]
    assert entries == [
        {"domain": "carrefouruae.com", "url": "oreo_300g.jpg", "reason": "size_conflict"},
        {"domain": "shutterstock.com", "url": "oreo-123.jpg", "reason": "stock_or_clipart"},
        {"domain": "noon.com", "url": "x.html", "reason": "download:not_image"},
    ]
    assert "hard_rejected_more" not in trace["outcome"]
    json.dumps(trace["outcome"])                     # stored as trace_json


def test_the_trace_caps_the_hard_rejections():
    rejected = [_rc(listing("Oreo Biscuits 300g", image=f"https://cdn.example.com/{'x' * 100}{i}.jpg"),
                    status="rejected") for i in range(facade.HARD_REJECTS_MAX + 5)]
    outcome = SearchOutcome(decision="NOT_FOUND", failure_code="ALL_CONFLICTED", ranked=rejected,
                            sku_key=SPEC.sku_key)
    trace = {}
    facade.outcome_to_legacy(outcome, trace, SPEC)
    entries = trace["outcome"]["hard_rejected"]
    assert len(entries) == facade.HARD_REJECTS_MAX
    assert trace["outcome"]["hard_rejected_more"] == 5
    assert all(len(e["url"]) <= facade.SHORT_URL_CHARS and set(e) == {"domain", "url", "reason"} for e in entries)
    assert {e["reason"] for e in entries} == {"size_conflict"}
