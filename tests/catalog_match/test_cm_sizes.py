"""catalog_match.sizes: net-content grammar and comparison (RANK-9, SRC-3, t4/g2 proofs)."""

import socket

import pytest

from catalog_match.models import Size
from catalog_match.sizes import AMBIGUOUS, compare, compare_pack, parse_sizes, product_size
from catalog_match.text_norm import url_path_text


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def one(text, field="title"):
    sizes = parse_sizes(text, field)
    assert len(sizes) == 1, (text, sizes)
    return sizes[0]


def target(text):
    ps = product_size(parse_sizes(text, "name"))
    assert isinstance(ps, Size), (text, ps)
    return ps


def test_grammar():
    s = one("24x330ml")
    assert (s.dimension, s.base_value, s.pack_count) == ("volume", 330.0, 24)
    s = one("12 x 1.5L")
    assert (s.dimension, s.base_value, s.pack_count) == ("volume", 1500.0, 12)
    assert one("6 x 180ml").pack_count == 6
    s = one("200غ")
    assert (s.dimension, s.base_value) == ("mass", 200.0)
    s = one("١ لتر")
    assert (s.dimension, s.base_value) == ("volume", 1000.0)
    assert one("Milk 1 Liter").base_value == 1000.0
    assert one("Milk 2 Litre").base_value == 2000.0
    s = one("A/G Ketchup 28oz")
    assert s.dimension == "mass" and s.base_value == pytest.approx(793.79, abs=0.01)


def test_grammar_more_forms():
    assert one("Pepsi 330ml x 24").pack_count == 24
    assert one("Laban 180ml x6").pack_count == 6
    assert one("Pepsi 6 cans x 330 ml").pack_count == 6
    assert one("Masafi Water 1.5L (Pack of 12)").pack_count == 12
    assert one("Laban 180ml 6 pcs").pack_count == 6
    assert one("Milk 1,5L").base_value == 1500.0                  # decimal comma
    assert one("Rice 1,000g").base_value == 1000.0                # thousands comma
    assert one("Juice 75cl").base_value == 750.0
    assert one("Coffee 12 fl oz").dimension == "volume"
    assert one("Flour 2 lbs").base_value == pytest.approx(907.18, abs=0.01)
    assert one("Oil 1.8 ltr").base_value == 1800.0
    assert one("Sugar 500 gms").base_value == 500.0
    assert one("لبن 180 مل").base_value == 180.0
    assert one("أرز 5 كجم").base_value == 5000.0
    assert one("حليب بودرة 2.25 كيلو").base_value == 2250.0
    assert one("جبنة 500 جرام").base_value == 500.0
    eggs = one("Eggs 30 pcs")
    assert (eggs.dimension, eggs.base_value) == ("count", 30.0)
    # words that merely start with a unit letter are not units
    assert parse_sizes("Pack of lemons", "title") == []
    assert parse_sizes("3 لبن", "title") == []


def test_nutrient_context_ignored():
    assert [s.base_value for s in parse_sizes("Oats 500g - 10g fibre", "title")] == [500.0]
    assert [s.base_value for s in parse_sizes("Corn Flakes 500g | 20g per serving", "title")] == [500.0]
    assert parse_sizes("per 100g: 5g sugar", "title") == []
    # a big net weight followed by 'protein' is still a size; 'fat free' is not a nutrient amount
    assert [s.base_value for s in parse_sizes("Whey 1kg protein powder", "title")] == [1000.0]
    assert [s.base_value for s in parse_sizes("Yoghurt 100g fat free", "title")] == [100.0]


def test_compare():
    assert compare(target("Laban 180ml"), parse_sizes("Laban 200ml", "title")) == "conflict"
    assert compare(target("Nido 2.25kg"), parse_sizes("Nido 2.5kg", "title")) == "conflict"
    assert compare(target("Ketchup 28oz"), parse_sizes("Ketchup 20oz", "title")) == "conflict"
    assert compare(target("Milk 1L"), parse_sizes("Milk 1000ml", "title")) == "match"
    assert compare(target("Rice 900g"), parse_sizes("Rice 0.9kg", "title")) == "match"
    assert compare(target("Oats 500g"), parse_sizes("Oats 500g - 10g fibre", "title")) == "match"
    assert compare(target("Rice 5kg"), parse_sizes("Rice 5kg, 10kg, 20kg", "title")) == AMBIGUOUS
    assert compare(target("Pepsi 2.25L"), parse_sizes("Pepsi 24x330ml", "title")) == "conflict"
    # within 3 %: rounding on a listing is not a different product
    assert compare(target("Water 1.5L"), parse_sizes("Water 1.49L", "title")) == "match"
    # nothing to compare
    assert compare(target("Milk 1L"), []) == "unknown"
    assert compare(None, parse_sizes("Milk 1L", "title")) == "unknown"
    # dimensions must agree: a mass is never compared with a volume
    assert compare(target("Laban 180ml"), parse_sizes("Laban 180g", "title")) == "unknown"


def test_compare_pack():
    single = None
    assert compare_pack(single, parse_sizes("Pepsi 24x330ml", "title")) == "conflict"
    assert compare_pack(single, parse_sizes("Masafi 12 x 1.5L", "title")) == "conflict"
    assert compare_pack(single, parse_sizes("Masafi Water 1.5L", "title")) == "unknown"
    assert compare_pack(6, parse_sizes("Laban 6 x 180ml", "title")) == "match"
    assert compare_pack(6, parse_sizes("Laban 4 x 180ml", "title")) == "conflict"
    assert compare_pack(6, parse_sizes("Laban 180ml", "title")) == "unknown"
    assert compare_pack(single, parse_sizes("Pepsi Can 6 pcs", "title")) == "conflict"
    # contents counted in the product ('100 tea bags') are not packs
    assert compare_pack(single, parse_sizes("Lipton 100 Tea Bags", "title")) == "unknown"


def test_product_size():
    assert product_size(parse_sizes("Rice 5kg, 10kg, 20kg", "title")) == AMBIGUOUS
    assert product_size(parse_sizes("Milk 1L - 1000 ml", "title")).base_value == 1000.0
    assert product_size(parse_sizes("Milk", "title")) is None
    assert target("Pepsi 6 x 330ml").canonical() == "6x330ml"
    assert target("Milk 1L").canonical() == "1000ml"


def test_fields_separate():
    assert parse_sizes("/p/1", "page_url") + parse_sizes("g.jpg", "image_url") == []
    # concatenating the two fields is exactly what invented '1 g' before
    assert [s.base_value for s in parse_sizes("/p/1" + "g.jpg", "joined")] == [1.0]
    assert parse_sizes("", "title") == []


def test_slug_decimals():
    # SRC-3: '1-5l' used to parse as 5 L and '2-85l' as 85 L
    masafi = parse_sizes(url_path_text("https://x.ae/al-ain-water-1-5l/p/7"), "page_slug")
    assert [s.base_value for s in masafi] == [1500.0]
    almarai = parse_sizes(url_path_text("https://x.ae/almarai-full-fat-2-85l"), "page_slug")
    assert [s.base_value for s in almarai] == [2850.0]
    multipack = parse_sizes(url_path_text("https://x.ae/masafi-12-x-1-5l"), "page_slug")
    assert [(s.base_value, s.pack_count) for s in multipack] == [(1500.0, 12)]
    assert compare(target("Al Ain Water 1.5L"), masafi) == "match"
