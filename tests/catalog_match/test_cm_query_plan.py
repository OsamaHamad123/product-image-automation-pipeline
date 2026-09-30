"""catalog_match.query_plan: the deterministic query plan (D3, D8, SRC-6).

Specs are built by the real identity.build_sku_spec from sheet-like rows.
"""

import re
import socket

import pytest

from catalog_match.gtin import gtin13
from catalog_match.identity import build_sku_spec
from catalog_match.query_plan import (
    MAX_PLANNED_QUERIES, RETAILER_SITES, build_queries, display_gtin, relaxations, size_token,
)
from catalog_match.sizes import parse_sizes, product_size
from catalog_match.text_norm import tokens


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


MAPPINGS = {
    "almarai": {"brand": "Almarai", "synonyms": ["Almarai", "Al Marai", "المراعي"],
                "excluded_competitors": ["Al Rawabi", "Nadec"], "official_domains": ["almarai.com"]},
    "nestle": {"brand": "Nestle", "synonyms": ["Nestle", "نستله"], "sub_brands": ["Nido", "KitKat"]},
    "american garden": {"brand": "American Garden", "synonyms": ["American Garden", "A/G", "AG", "أمريكان جاردن"]},
    "pepsi": {"brand": "Pepsi", "synonyms": ["Pepsi", "بيبسي"]},
}
VALID_EAN = "6281007035224"


def spec_for(**row):
    return build_sku_spec(row, MAPPINGS)


_SITE_OP = re.compile(r"\bsite:\S+", re.I)


def search_terms(text):
    """The query minus site: operators ('site:almarai.com' restricts a domain, it is not a keyword)."""
    return _SITE_OP.sub(" ", text)


def count_token(text, token):
    return tokens(search_terms(text), strip_clitics=True).count(token)


def count_phrase(text, phrase):
    toks, p = tokens(search_terms(text), strip_clitics=True), tokens(phrase, strip_clitics=True)
    return sum(1 for i in range(len(toks) - len(p) + 1) if toks[i:i + len(p)] == p)


def test_brand_exactly_once_per_query():
    specs = [
        spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai"),
        spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai", barcode=VALID_EAN),
        spec_for(name="Fresh Milk Full Fat 1L", brand="Almarai"),                 # brand not in the name
        spec_for(name="Al Marai Fresh Milk Almarai 1 Ltr", brand="Al Marai"),     # synonym + repeat
        spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="المراعي"),          # Arabic sheet brand
    ]
    for spec in specs:
        plan = build_queries(spec) + relaxations(spec)
        assert plan
        for q in plan:
            assert count_token(q.text, "almarai") == 1, (spec.raw_name, q)
            assert count_phrase(q.text, "al marai") == 0, (spec.raw_name, q)

    plan = build_queries(spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai"))
    assert plan[0].query_id == "Q1"
    assert plan[0].text == "Almarai Fresh Milk Full Fat 1L"


def test_size_token_included_whenever_the_sku_has_a_size():
    cases = [
        (spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai"), "1L"),
        (spec_for(name="Almarai Fresh Milk Full Fat", brand="Almarai", size="1 Litre"), "1L"),   # size column
        (spec_for(name="Pepsi Cola 24 x 330 ml", brand="Pepsi"), "24x330ml"),
        (spec_for(name="Al Rawabi Laban Up 180ml (Pack of 6)", brand="Al Rawabi"), "6x180ml"),
        (spec_for(name="Nido Fortified Milk Powder 2.25kg", brand="Nestle"), "2.25kg"),
    ]
    for spec, expected in cases:
        assert spec.size is not None
        for q in build_queries(spec):
            if q.query_id == "Q4":
                continue  # the brand + GTIN query identifies the pack by barcode
            assert expected in q.text, q
            # Exactly one size statement survives: the name's own size words were replaced by the token.
            stated = product_size(parse_sizes(search_terms(q.text)))
            assert (stated.dimension, stated.base_value, stated.pack_count) == (
                spec.size.dimension, spec.size.base_value, spec.size.pack_count), q
            assert "pack of" not in q.text.lower()


def test_custom_query_replaces_the_plan():
    spec = spec_for(name="Almarai Fresh Milk Full Fat 1L", name_ar="حليب المراعي طازج 1 لتر",
                    brand="Almarai", barcode=VALID_EAN)
    custom = "Almarai Full Cream Milk 1L site:carrefouruae.com"
    plan = build_queries(spec, custom_query=custom)
    assert len(plan) == 1
    assert plan[0].query_id == "custom"
    assert plan[0].text == custom
    assert plan[0].providers_hint == ()
    assert plan[0].relaxed is False

    ar = build_queries(spec, custom_query="  حليب المراعي   1 لتر ")
    assert [(q.query_id, q.text, q.hl) for q in ar] == [("custom", "حليب المراعي 1 لتر", "ar")]
    # A blank custom query is no custom query.
    assert [q.query_id for q in build_queries(spec, custom_query="   ")][0] == "Q1"


def test_arabic_query_only_when_name_ar_is_set():
    without = build_queries(spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai"))
    assert all(q.query_id != "Q2" for q in without)
    assert all(q.hl == "en" for q in without)

    with_ar = build_queries(spec_for(name="Almarai Fresh Milk Full Fat 1L",
                                     name_ar="حليب المراعي الطازج كامل الدسم 1 لتر", brand="Almarai"))
    q2 = [q for q in with_ar if q.query_id == "Q2"]
    assert len(q2) == 1
    assert q2[0].hl == "ar"
    assert count_token(q2[0].text, "مراعي") == 1          # Arabic brand exactly once
    assert "حليب" in q2[0].text and "1 لتر" in q2[0].text

    # An 'Arabic' column holding Latin text is not an Arabic name.
    latin_only = build_queries(spec_for(name="Almarai Fresh Milk 1L", name_ar="Almarai Fresh Milk 1L",
                                        brand="Almarai"))
    assert all(q.query_id != "Q2" for q in latin_only)


def test_no_query_is_the_bare_gtin():
    spec = spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai", barcode=VALID_EAN)
    assert spec.gtin == "0" + VALID_EAN
    forms = {spec.gtin, gtin13(spec.gtin), VALID_EAN}
    plan = build_queries(spec) + relaxations(spec)
    for q in plan:
        assert q.text.strip().strip('"') not in forms
        assert not q.text.replace(" ", "").isdigit()
    q4 = [q for q in plan if q.query_id == "Q4"]
    assert len(q4) == 1
    assert q4[0].text == f'"Almarai" {VALID_EAN}'

    # A barcode typed as the custom query is still never sent bare.
    custom = build_queries(spec, custom_query=VALID_EAN)
    assert [q.text for q in custom] == [f'"Almarai" {VALID_EAN}']
    # No brand known: the name carries the barcode instead.
    unbranded = spec_for(name="Tomato Paste 400g", brand="", barcode=VALID_EAN)
    assert all(q.query_id != "Q4" for q in build_queries(unbranded))
    assert build_queries(unbranded, custom_query=VALID_EAN)[0].text == f"Tomato Paste {VALID_EAN}"


def test_q4_only_for_a_valid_gtin():
    bad = spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai", barcode="6281007035225")
    assert bad.gtin is None
    assert all(q.query_id != "Q4" for q in build_queries(bad))


def test_at_most_four_planned_queries_in_order():
    spec = spec_for(name="Almarai Fresh Milk Full Fat 1L", name_ar="حليب المراعي كامل الدسم 1 لتر",
                    brand="Almarai", barcode=VALID_EAN)
    plan = build_queries(spec)
    assert len(plan) <= MAX_PLANNED_QUERIES
    assert [q.query_id for q in plan] == ["Q1", "Q2", "Q3", "Q4"]
    assert all(not q.relaxed for q in plan)


def test_q3_is_site_scoped_and_serper_only():
    spec = spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai")
    q3 = [q for q in build_queries(spec) if q.query_id == "Q3"][0]
    assert q3.providers_hint == ("serper",)
    assert q3.text.startswith("Almarai Fresh Milk Full Fat 1L (")
    for site in ("almarai.com",) + RETAILER_SITES:     # official domain first, then UAE retailers
        assert f"site:{site}" in q3.text
    assert " OR " in q3.text
    others = [q for q in build_queries(spec) if q.query_id != "Q3"]
    assert all(q.providers_hint == () for q in others)


def test_sub_brand_kept_and_short_synonym_replaced():
    nido = build_queries(spec_for(name="Nido Fortified Milk Powder 2.25kg", brand="Nestle"))[0]
    assert nido.text == "Nestle Nido Fortified Milk Powder 2.25kg"
    nido_as_brand = build_queries(spec_for(name="Nido Fortified Milk Powder 2.25kg", brand="Nido"))[0]
    assert count_token(nido_as_brand.text, "nido") == 1
    assert count_token(nido_as_brand.text, "nestle") == 1

    ag = build_queries(spec_for(name="A/G Mayonnaise 946ml", brand="A/G"))[0]
    assert ag.text == "American Garden Mayonnaise 946ml"


def test_name_words_are_kept_when_the_size_is_ambiguous():
    spec = spec_for(name="Al Ain Water 1,5L + 500ml Bundle", brand="Al Ain")
    assert spec.size is None                      # two sizes: nothing to normalise
    q1 = build_queries(spec)[0].text
    assert "1,5L" in q1 and "500ml" in q1         # the comma decimal is not split into '1' '5L'


def test_arabic_sheet_name_gives_an_arabic_first_query():
    spec = spec_for(name="حليب المراعي 1 لتر", brand="المراعي")
    q1 = build_queries(spec)[0]
    assert q1.hl == "ar"
    assert q1.text == "المراعي حليب 1 لتر"


def test_relaxations():
    spec = spec_for(name="Almarai Fresh Milk Full Fat 1L", brand="Almarai")
    rel = {q.query_id: q for q in relaxations(spec)}
    assert set(rel) == {"R1", "R2"}
    assert all(q.relaxed for q in rel.values())
    r1_tokens = tokens(rel["R1"].text)
    assert not {"fresh", "full", "fat"} & set(r1_tokens)       # R1: no variant words
    assert "1L" in rel["R1"].text                               # ... but keeps the size
    assert "1L" not in rel["R2"].text and "1" not in tokens(rel["R2"].text)   # R2: no size
    assert "full" in tokens(rel["R2"].text)                     # ... but keeps the variant
    for q in rel.values():
        assert count_token(q.text, "almarai") == 1

    # Nothing to relax: no variants and no size.
    assert relaxations(spec_for(name="Almarai Milk", brand="Almarai")) == []
    # A relaxation that would leave only the brand is not produced.
    assert [q.query_id for q in relaxations(spec_for(name="Pepsi 24x330ml", brand="Pepsi"))] == []


def test_size_token_and_display_gtin_forms():
    spec = spec_for(name="Almarai Laban 2.5L", brand="Almarai")
    assert size_token(spec.size) == "2.5L"
    assert size_token(spec.size, "ar") == "2.5 لتر"
    assert size_token(spec_for(name="Tea 100 Tea Bags", brand="").size) == "100 tea bags"
    assert size_token(spec_for(name="Syrup 12 fl oz", brand="").size) == "12 fl oz"
    assert size_token(spec_for(name="Rice 500g", brand="").size) == "500g"
    assert display_gtin("06281007035224") == "6281007035224"   # EAN-13
    assert display_gtin("00012345678905") == "012345678905"    # UPC-A
    assert display_gtin("00000096385074") == "96385074"        # EAN-8
