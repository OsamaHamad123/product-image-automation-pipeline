"""catalog_match.brand_index: reverse synonym index from the Brands Mapping sheet (D9, SRC-10, MISS-5)."""

import socket

import pytest

from catalog_match.brand_index import BrandIndex, build_index
from catalog_match.text_norm import normalize


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _row(brand, synonyms, competitors, **extra):
    syns = [s.strip() for s in synonyms.split(",") if s.strip()]
    if brand not in syns:          # get_brand_mappings() puts the brand itself first
        syns.insert(0, brand)
    row = {"brand": brand, "synonyms": syns,
           "excluded_competitors": [c.strip() for c in competitors.split(",") if c.strip()]}
    row.update(extra)
    return row


# The default 'Brands Mapping' rows written by google_sheets.get_brand_mappings(), plus
# American Garden / Nestle (with sub-brands) / Heinz as the plan's test mapping.
MAPPINGS = {
    "meliha": _row("Meliha", "Mleiha, مليحة, مليحه", "Almarai, Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada"),
    "saba sanabel": _row("Saba Sanabel", "Sabaa Sanabel, سبع سنابل, صبا سنابل, سنابل", "Al Baker, Jenan, Grand Mills, Organic Larder"),
    "mai dubai": _row("Mai Dubai", "May Dubai, ماي دبي, مي دبي, مياه دبي", "Masafi, Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Voss, Evian"),
    "almarai": _row("Almarai", "Al Marai, المراعي", "Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada, Meliha, Mleiha"),
    "masafi": _row("Masafi", "مسافي", "Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian"),
    "al ain": _row("Al Ain", "العين, alain", "Masafi, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian"),
    "american garden": _row("American Garden", "A/G, AG, أمريكان جاردن", "Heinz"),
    "nestle": _row("Nestle", "نستله", "", sub_brands=["Nido", "KitKat"], official_domains=["https://www.nestle-mena.com/en"]),
    "heinz": _row("Heinz", "هاينز", "American Garden"),
}


@pytest.fixture(scope="module")
def index():
    return BrandIndex.from_mappings(MAPPINGS)


def test_cm_brand_index(index):
    r = index.resolve("المراعي")
    assert (r.canonical, r.conf) == ("Almarai", "mapped")
    assert index.resolve("Al Marai").canonical == "Almarai"

    ag = index.resolve("A/G")
    assert (ag.canonical, ag.conf) == ("American Garden", "mapped")
    assert "ag" not in ag.match_brands
    assert "a g" not in ag.match_brands
    assert "american garden" in ag.match_brands

    nido = index.resolve("Nestle", name_en="Nido Fortified Milk Powder 2.25kg")
    assert nido.canonical == "Nestle"
    assert "nido" in nido.match_brands

    tomato = index.resolve("Tomato")
    assert tomato.conf == "sheet_raw"
    assert tomato.canonical == "Tomato"

    assert index.resolve("").conf == "none"
    assert index.resolve("", name_en="Tomato Paste 400g").conf == "none"   # no first-word invention


def test_lookup_is_normalised_both_ways(index):
    # SRC-10 / p7: the sheet brand may be any spelling in the synonym list
    for spelling in ("ALMARAI", "almarai", "Al-Marai", "إلمراعى", " المراعي "):
        assert index.resolve(spelling).canonical == "Almarai", spelling
    assert index.resolve("AG").canonical == "American Garden"
    assert index.resolve("american garden").canonical == "American Garden"
    assert index.resolve("Mleiha").canonical == "Meliha"
    assert index.resolve("مليحة").canonical == "Meliha"      # ta marbuta folding
    assert index.resolve("Alain").canonical == "Al Ain"


def test_short_synonyms_resolve_but_never_match(index):
    for r in (index.resolve("A/G"), index.resolve("AG"), index.resolve("American Garden")):
        assert all(sum(ch.isalnum() for ch in p) >= 3 for p in r.match_brands)
        assert "ag" not in r.match_brands
    assert "ag" not in index.known_brands()


def test_sub_brands(index):
    # only the sub-brand the product name uses is added
    kitkat = index.resolve("Nestle", name_en="KitKat 4 Finger 41.5g")
    assert "kitkat" in kitkat.match_brands and "nido" not in kitkat.match_brands
    plain = index.resolve("Nestle", name_en="Fitness Cereal 375g")
    assert "nido" not in plain.match_brands and "kitkat" not in plain.match_brands
    # a sheet brand that is itself a sub-brand resolves to the parent and keeps the sub-brand
    r = index.resolve("Nido")
    assert r.canonical == "Nestle" and r.conf == "mapped" and "nido" in r.match_brands
    # sibling sub-brands are not competitors of their parent
    assert "kitkat" not in kitkat.competitors and "nido" not in plain.competitors


def test_name_start_fallback(index):
    r = index.resolve("", name_en="Almarai Fresh Milk Full Fat 1L")
    assert (r.canonical, r.conf) == ("Almarai", "mapped")
    r = index.resolve("", name_en="Nido Fortified Milk Powder 2.25kg")
    assert r.canonical == "Nestle" and "nido" in r.match_brands
    r = index.resolve("", name_ar="المراعي حليب طازج")
    assert r.canonical == "Almarai"
    # a brand in the middle of the name is not used
    assert index.resolve("", name_en="Fresh Milk by Almarai").conf == "none"


def test_competitors(index):
    almarai = index.resolve("Almarai")
    assert "nada" in almarai.competitors
    assert "al rawabi" in almarai.competitors
    assert "heinz" in almarai.competitors                  # every other known brand counts
    assert "almarai" not in almarai.competitors and "al marai" not in almarai.competitors
    masafi = index.resolve("Masafi")
    assert "al ain" in masafi.competitors
    # an unmapped sheet brand never lists a brand nested in its own name as a competitor
    raw = index.resolve("Almarai Dairy Co")
    assert raw.conf == "sheet_raw"
    assert "almarai" not in raw.competitors
    assert "masafi" in raw.competitors


def test_known_brands_and_domains(index):
    known = index.known_brands()
    for phrase in ("almarai", "al marai", "american garden", "nido", "kitkat", "heinz", "nada", normalize("المراعي")):
        assert phrase in known, phrase
    assert index.resolve("Nestle").official_domains == ("nestle-mena.com",)
    assert index.resolve("Almarai").brand_ar == "المراعي"


def test_mapping_shapes():
    # comma-separated strings (a raw sheet cell) and missing optional keys are accepted
    idx = build_index({"kiri": {"brand": "Kiri", "synonyms": "كيري, Kiri Cheese", "excluded_competitors": "Puck، President"}})
    assert idx.resolve("كيري").canonical == "Kiri"
    assert "puck" in idx.resolve("Kiri").competitors
    assert "president" in idx.resolve("Kiri").competitors
    assert build_index(None).resolve("Kiri").conf == "sheet_raw"
    assert build_index(idx) is idx
