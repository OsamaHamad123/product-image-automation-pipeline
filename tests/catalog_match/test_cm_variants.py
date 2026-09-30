"""catalog_match.variants: exclusive variant groups in English and Arabic (RANK-5, MISS-3)."""

import socket

import pytest

from catalog_match.variants import (
    conflicts,
    extract_variants,
    matched_axes,
    merge,
    unstated_marked,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_cm_variants():
    full_cream = extract_variants("Almarai Full Cream Milk 1L")
    low_fat = extract_variants("Almarai Low Fat Milk 1L")
    assert full_cream == {"fat": "full"}
    assert low_fat == {"fat": "low"}
    assert conflicts(full_cream, low_fat) == ["fat"]

    assert conflicts(extract_variants("حليب كامل الدسم"), extract_variants("Milk Full Fat")) == []
    assert extract_variants("حليب كامل الدسم") == {"fat": "full"}

    assert conflicts(extract_variants("Laban strawberry"), extract_variants("Laban mango")) == ["flavour"]

    fresh = extract_variants("Almarai Fresh Milk")
    assert "fat" not in fresh
    assert conflicts(fresh, extract_variants("Almarai Fresh Milk Full Fat 1L")) == []
    assert conflicts(fresh, extract_variants("Almarai Low Fat Fresh Milk 1L")) == []


def test_longest_phrase_wins():
    # 'semi skimmed' is low fat, not skimmed; 'fat free' is skimmed, not a 'fat' mention
    assert extract_variants("Semi-Skimmed Milk") == {"fat": "low"}
    assert extract_variants("Fat Free Yoghurt") == {"fat": "skimmed"}
    assert extract_variants("Coca Cola Zero Sugar") == {"sugar": "zero"}
    assert extract_variants("Yoghurt Zero Fat") == {"fat": "skimmed"}


def test_arabic_variants_with_clitics():
    assert extract_variants("لبن بالفراولة") == {"flavour": "strawberry"}
    assert extract_variants("عصير المانجو") == {"flavour": "mango"}
    assert extract_variants("حليب قليل الدسم") == {"fat": "low"}
    assert extract_variants("قهوة منزوعة الكافيين") == {"caffeine": "decaf"}   # feminine agreement
    assert extract_variants("قهوة منزوع الكافيين") == {"caffeine": "decaf"}
    assert extract_variants("حليب خالي الدسم") == {"fat": "skimmed"}
    assert conflicts(extract_variants("لبن بنكهة الفراولة"), extract_variants("Strawberry Laban")) == []
    assert conflicts(extract_variants("لبن بنكهة الفراولة"), extract_variants("Mango Laban")) == ["flavour"]


def test_plain_is_never_inferred():
    plain = extract_variants("Almarai Laban Plain 180ml")
    strawberry = extract_variants("Almarai Laban Strawberry 180ml")
    unstated = extract_variants("Almarai Laban 180ml")
    assert unstated == {}
    # target says plain -> a flavour conflicts; target silent -> no conflict
    assert conflicts(plain, strawberry) == ["flavour"]
    assert conflicts(unstated, strawberry) == []
    # dossier s4: target strawberry vs the sharper 'Plain' sibling conflicts
    assert conflicts(strawberry, plain) == ["flavour"]


def test_multi_value_listing_is_not_a_conflict_nor_a_match():
    listing = extract_variants("Milk - Full Fat / Low Fat / Skimmed")
    assert listing == {"fat": "full+low+skimmed"}
    assert conflicts({"fat": "full"}, listing) == []
    assert matched_axes({"fat": "full"}, listing) == []
    assert matched_axes({"fat": "full"}, extract_variants("Full Fat Milk")) == ["fat"]
    combo = extract_variants("Strawberry & Banana Smoothie")
    assert combo == {"flavour": "banana+strawberry"}
    assert matched_axes(combo, extract_variants("Banana Strawberry smoothie")) == ["flavour"]


def test_other_axes():
    assert conflicts(extract_variants("Pepsi Diet"), extract_variants("Pepsi Regular")) == ["sugar"]
    assert extract_variants("Nescafe Gold Decaf") == {"caffeine": "decaf"}
    assert conflicts(extract_variants("Nido Milk Powder"), extract_variants("Nido Long Life Milk UHT")) == ["form"]
    assert "fat" not in extract_variants("Whole Wheat Bread")        # 'whole' alone is not full fat
    assert extract_variants("Whole Milk") == {"fat": "full"}
    assert extract_variants("") == {}
    assert merge({"fat": "full"}, {"flavour": "mango"}, {"fat": "full"}) == {"fat": "full", "flavour": "mango"}


def test_unstated_marked():
    assert unstated_marked({}, extract_variants("Almarai Laban Strawberry")) == ["flavour"]
    assert unstated_marked({}, extract_variants("Pepsi Diet 330ml")) == ["sugar"]
    assert unstated_marked({}, extract_variants("Almarai Low Fat Milk")) == ["fat"]
    # defaults are not suspicious
    assert unstated_marked({}, extract_variants("Almarai Full Fat Milk")) == []
    assert unstated_marked({}, extract_variants("Almarai Laban Plain")) == []
    assert unstated_marked({"fat": "full"}, extract_variants("Almarai Low Fat Milk")) == []
