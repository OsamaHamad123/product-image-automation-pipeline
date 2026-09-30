"""catalog_match.identity: sheet row -> SkuSpec (D9, D11, SRC-9, SHEET-2)."""

import socket

import pytest

from catalog_match.brand_index import BrandIndex
from catalog_match.identity import build_sku_spec


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


MAPPINGS = {
    "almarai": {"brand": "Almarai", "synonyms": ["Almarai", "Al Marai", "المراعي"],
                "excluded_competitors": ["Al Rawabi", "Nadec", "Nada", "Meliha"]},
    "masafi": {"brand": "Masafi", "synonyms": ["Masafi", "مسافي"], "excluded_competitors": ["Al Ain", "Mai Dubai"]},
    "al ain": {"brand": "Al Ain", "synonyms": ["Al Ain", "العين", "alain"], "excluded_competitors": ["Masafi"]},
    "american garden": {"brand": "American Garden", "synonyms": ["American Garden", "A/G", "AG", "أمريكان جاردن"],
                        "excluded_competitors": ["Heinz"]},
    "nestle": {"brand": "Nestle", "synonyms": ["Nestle"], "excluded_competitors": [], "sub_brands": ["Nido", "KitKat"]},
    "heinz": {"brand": "Heinz", "synonyms": ["Heinz"], "excluded_competitors": ["American Garden"]},
}


def test_cm_identity():
    raw = "  Almarai Fresh Milk Full Fat 1L (Pack of 1)  "
    spec = build_sku_spec({"name": raw, "brand": "Almarai"}, MAPPINGS)
    assert spec.raw_name == raw

    valid = build_sku_spec({"name": "Drinking Water 500ml", "brand": "Mai Dubai", "barcode": "6297000611365"}, MAPPINGS)
    assert valid.gtin == "06297000611365"
    assert valid.sku_key == "06297000611365"
    assert valid.gtin_status == "ok"

    one_l = build_sku_spec({"name": "Almarai Fresh Milk", "brand": "Almarai", "size": "1L"}, MAPPINGS)
    two_l = build_sku_spec({"name": "Almarai Fresh Milk", "brand": "Almarai", "size": "2L"}, MAPPINGS)
    assert one_l.sku_key != two_l.sku_key
    assert (one_l.size.base_value, two_l.size.base_value) == (1000.0, 2000.0)

    sci = build_sku_spec({"name": "Masafi Water 1.5L", "brand": "Masafi", "barcode": "6.29E+12"}, MAPPINGS)
    assert sci.gtin is None
    assert sci.gtin_status == "scientific_notation"
    assert sci.gtin_raw == "6.29E+12"
    assert len(sci.sku_key) == 16


def test_brand_fields():
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "المراعي"}, MAPPINGS)
    assert spec.brand_raw == "المراعي"
    assert spec.brand_canonical == "Almarai"
    assert spec.brand_conf == "mapped"
    assert "almarai" in spec.match_brands
    assert "nada" in spec.competitors
    assert spec.brand_ar == "المراعي"

    ag = build_sku_spec({"name": "A/G Tomato Ketchup 340g", "brand": "A/G"}, MAPPINGS)
    assert ag.brand_canonical == "American Garden" and "ag" not in ag.match_brands

    nido = build_sku_spec({"name": "Nido Fortified Milk Powder 2.25kg", "brand": "Nestle"}, MAPPINGS)
    assert "nido" in nido.match_brands and "kitkat" not in nido.match_brands

    unknown = build_sku_spec({"name": "Tomato Paste 400g", "brand": ""}, MAPPINGS)
    assert unknown.brand_conf == "none" and unknown.brand_canonical == "" and unknown.match_brands == ()

    # an unmapped English brand with a mapped Arabic brand column resolves through the Arabic one
    ar = build_sku_spec({"name": "Fresh Laban 1L", "brand": "Almraai", "brand_ar": "المراعي"}, MAPPINGS)
    assert ar.brand_canonical == "Almarai" and ar.brand_conf == "mapped"

    raw = build_sku_spec({"name": "Al Rawabi Laban Up 180ml", "brand": "Al Rawabi"}, MAPPINGS)
    assert raw.brand_conf == "sheet_raw" and raw.match_brands == ("al rawabi",)


def test_size_variants_and_class_tokens():
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai"}, MAPPINGS)
    assert spec.size.base_value == 1000.0 and spec.size.dimension == "volume"
    assert spec.variants == {"fat": "full"}
    assert spec.class_tokens == ("milk",)

    laban = build_sku_spec({"name": "Al Rawabi Laban Up 180ml", "brand": "Al Rawabi"}, MAPPINGS)
    assert laban.class_tokens == ("laban", "up")

    nido = build_sku_spec({"name": "Nido Fortified Milk Powder 2.25kg", "brand": "Nestle"}, MAPPINGS)
    assert nido.variants == {"form": "powder"}
    assert nido.class_tokens == ("fortified", "milk")
    assert nido.size.base_value == 2250.0

    pack = build_sku_spec({"name": "Almarai Laban 6 x 180ml", "brand": "Almarai"}, MAPPINGS)
    assert pack.pack_count == 6 and pack.size.base_value == 180.0

    arabic = build_sku_spec({"name": "", "name_ar": "حليب المراعي بالفراولة ١ لتر", "brand": "المراعي"}, MAPPINGS)
    assert arabic.size.base_value == 1000.0
    assert arabic.variants == {"flavour": "strawberry"}
    assert arabic.class_tokens == ("حليب",)

    both = build_sku_spec({"name": "Almarai Milk Strawberry", "name_ar": "حليب المراعي بالفراولة", "brand": "Almarai"}, MAPPINGS)
    assert both.variants == {"flavour": "strawberry"}
    assert set(both.class_tokens) == {"milk", "حليب"}


def test_size_column_precedence():
    # the size column wins over a size in the name
    spec = build_sku_spec({"name": "Almarai Fresh Milk 2L", "brand": "Almarai", "size": "1 Litre"}, MAPPINGS)
    assert spec.size.base_value == 1000.0
    # an explicit size_text argument wins over the column
    spec = build_sku_spec({"name": "Almarai Fresh Milk", "brand": "Almarai", "size": "1L"}, MAPPINGS, size_text="500 ml")
    assert spec.size.base_value == 500.0
    # an unparseable or multi-size column falls back to the name
    spec = build_sku_spec({"name": "Tilda Basmati Rice 5kg", "brand": "Tilda", "size": "Large"}, MAPPINGS)
    assert spec.size.base_value == 5000.0
    spec = build_sku_spec({"name": "Tilda Basmati Rice 5kg", "brand": "Tilda", "size": "5kg, 10kg"}, MAPPINGS)
    assert spec.size.base_value == 5000.0
    # a name listing several sizes yields no size rather than a guess
    spec = build_sku_spec({"name": "Tilda Rice 5kg / 10kg", "brand": "Tilda"}, MAPPINGS)
    assert spec.size is None


def test_sku_key_rules():
    a = build_sku_spec({"name": "Almarai Fresh Milk 1L", "brand": "Almarai"}, MAPPINGS)
    b = build_sku_spec({"name": "Almarai Fresh Milk 1L", "brand": "Al Marai"}, MAPPINGS)
    c = build_sku_spec({"name": "Almarai Fresh Milk 1L", "brand": "Almarai"}, BrandIndex.from_mappings(MAPPINGS))
    assert a.sku_key == b.sku_key == c.sku_key               # canonical brand, not the spelling
    d = build_sku_spec({"name": "Almarai Fresh Milk 1L", "brand": "Almarai", "barcode": "123"}, MAPPINGS)
    assert d.gtin is None and d.gtin_status == "bad_length" and d.sku_key == a.sku_key
    e = build_sku_spec({"name": "Almarai Fresh Milk 1L Low Fat", "brand": "Almarai"}, MAPPINGS)
    assert e.sku_key != a.sku_key
    # no mappings at all still works
    f = build_sku_spec({"name": "Almarai Fresh Milk 1L", "brand": "Almarai", "category": "Dairy"}, None)
    assert f.brand_conf == "sheet_raw" and f.category == "Dairy" and f.competitors == ()
