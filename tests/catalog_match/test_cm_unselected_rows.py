"""Live run of 2026-10-03 (runs/2026-10-03/smoke_6.json): the 27 rows left without a pick, defect by defect.

Every test is built from the real sheet row (and, where a listing matters, the real listing title and
page) it cites. The sheet's sku_key of every one of the 60 rows must stay exactly what it was: the
names are read the way the stores write them for parsing and queries only.
"""
import csv
import socket
from pathlib import Path

import pytest

from catalog_match import settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, VlmImageVerdict
from catalog_match.score import score_candidate
from catalog_match.variants import extract_variants
from catalog_match.verify import MATCH, MISMATCH, classify

REPO = Path(__file__).resolve().parents[2]
RUN = REPO / "runs" / "2026-10-03"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def spec_of(name, brand, mappings=None):
    return build_sku_spec({"name": name, "brand": brand}, mappings or {})


def listing(title, page_url, image_url="https://img.example-cdn.com/1.jpg", provider="serper"):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider=provider,
                     query_id="Q1", rank=1)


def suggested_mappings():
    import google_sheets

    with open(RUN / "brands_mapping_suggested.csv", encoding="utf-8-sig", newline="") as fh:
        return google_sheets.parse_brand_mapping_rows([r for r in csv.reader(fh)])


# ---------------------------------------------------------------------------
# 5. What a luncheon meat or a masala is made of (the protein axis)
# ---------------------------------------------------------------------------

ZWAN_BEEF = ("ZWAN BEEF LUNCHEON MEAT 850GM", "ZWAN")                      # row 61
ALLDE_MEAT = ("ALLDE MEAT MASALA 160GM", "ALLDE")                           # row 25


def test_row61_the_chicken_luncheon_meat_is_another_product():
    spec = spec_of(*ZWAN_BEEF)
    assert spec.variants == {"protein": "beef"}
    chicken = score_candidate(spec, listing("Zwan Chicken Luncheon Meat 850 g Online at Best Price | Lulu UAE",
                                            "https://gcc.luluhypermarket.com/en-ae/zwan-chicken-luncheon-meat-850-g/p/1"))
    assert chicken.tier != 1 and "variant_conflict:protein" in chicken.hard_reject      # was tier 1
    beef = score_candidate(spec, listing("Zwan Zwan Beef Luncheon Meat Can - 850gms | Best Price UAE",
                                         "https://www.noon.com/uae-en/zwan-beef-luncheon-meat-can-850gms/p/2"))
    assert beef.tier == 1
    unsaid = score_candidate(spec, listing("Zwan Luncheon Meat, 850g",
                                           "https://www.carrefouruae.com/mafuae/en/zwan-luncheon-meat-850g/p/3"))
    assert unsaid.tier == 2 and not unsaid.hard_reject                  # the listing does not say beef


def test_row25_chicken_masala_is_not_meat_masala():
    spec = spec_of(*ALLDE_MEAT)
    assert spec.variants == {"protein": "meat"} and spec.class_tokens == ("masala",)
    page = "https://www.carrefouruae.com/mafuae/en/allde-{}-masala-160g/p/1"
    chicken = score_candidate(spec, listing("Allde Chicken Masala 160g | Carrefour UAE", page.format("chicken")))
    assert chicken.tier != 1 and "variant_conflict:protein" in chicken.hard_reject      # was tier 1
    assert score_candidate(spec, listing("Allde Meat Masala 160g | Carrefour UAE", page.format("meat"))).tier == 1
    # mutton masala is a close line (soft): never tier 1, never a hard reject
    mutton = score_candidate(spec, listing("Allde Mutton Masala 160g | Carrefour UAE", page.format("mutton")))
    assert mutton.tier == 2 and any(c.startswith("soft_variant_conflict:protein") for c in mutton.conflicts)


def test_the_label_reader_is_held_to_the_protein_too():
    spec = spec_of(*ZWAN_BEEF)
    read = VlmImageVerdict(index=0, brand_text="ZWAN", variant_text="CHICKEN Luncheon Meat", size_text="850 g",
                           view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes")
    assert classify(spec, read) == MISMATCH
    read.variant_text = "BEEF Luncheon Meat"
    assert classify(spec, read) == MATCH


def test_meat_in_luncheon_meat_is_the_product_not_the_protein():
    spec = spec_of("TARGET LUNCHEON MEAT 340GM", "TARGET")
    assert "protein" not in spec.variants and "meat" in spec.class_tokens
    # a listing that names the animal the sheet leaves out is held for review, never rejected
    s = score_candidate(spec, listing("Target Beef Luncheon Meat 340 Gm: Buy Online at Best Price in UAE - Amazon.ae",
                                      "https://www.amazon.ae/Target-Beef-Luncheon-Meat-340/dp/B0"))
    assert s.tier == 2 and "unstated_variant:protein" in s.conflicts


@pytest.mark.parametrize("text, context, protein", [
    ("Mutton Meat Masala", None, "mutton"),             # 'meat' is generic: the animal wins
    ("Beef Luncheon Meat", None, "beef"),
    ("مرتديلا لحم بقري 340 جم", None, "beef"),
    ("ماسالا الدجاج", None, "chicken"),
    ("Meat Spice 200ml", None, "meat"),
    ("Chicken Flavour Instant Noodles 75g", None, None),  # not a meat product: no protein axis
    ("Light Meat Tuna In Water 170g", "tuna", None),
    ("Breaded Chicken Nuggets 400g", None, None),
])
def test_protein_is_read_only_where_it_names_the_product(text, context, protein):
    assert extract_variants(text, context).get("protein") == protein


def test_the_suggested_mapping_keeps_zwan_beef_and_chicken_apart():
    spec = spec_of(*ZWAN_BEEF, mappings=suggested_mappings())
    assert spec.brand_conf == "mapped" and spec.variants == {"protein": "beef"}
