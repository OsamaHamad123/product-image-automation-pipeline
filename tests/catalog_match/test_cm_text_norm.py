"""catalog_match.text_norm: normalisation and token-boundary matching (D9, RC-3)."""

import socket

import pytest

from catalog_match.text_norm import (
    match_string,
    normalize,
    phrase_in,
    strip_arabic_clitics,
    tokens,
    url_host,
    url_key,
    url_path_text,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_boundaries():
    heinz_url = url_path_text("https://cdn.shop.com/images/heinz-ketchup-package.jpg")
    # dossier p6: the synonym 'AG' matched 'images' / 'package'
    assert phrase_in("ag", heinz_url) is False
    # dossier s1 case 3: competitor 'Nada' matched 'canada'
    assert phrase_in("nada", "almarai full fat milk canada") is False
    # RANK-5: keyword 'rice' matched 'price', brand 'tang' matched 'tangerine'
    assert phrase_in("rice", "tilda price offers") is False
    assert phrase_in("tang", "al rawabi tangerine juice") is False
    # MISS-2: a multi-word brand must match a hyphenated slug
    assert phrase_in("al rawabi", url_path_text("https://x.ae/al-rawabi-laban-up-180ml/p/1")) is True


def test_boundaries_positive_controls():
    # the same texts DO contain the real words, so the negatives above are not trivially False
    heinz_url = url_path_text("https://cdn.shop.com/images/heinz-ketchup-package.jpg")
    assert phrase_in("heinz", heinz_url)
    assert phrase_in("Heinz Ketchup", heinz_url)
    assert phrase_in("nada", "Nada Fresh Milk 1L")
    assert phrase_in("rice", "Tilda Basmati Rice 5kg")
    assert phrase_in("tang", "Tang Orange Drink Powder")
    assert phrase_in("canada", "almarai full fat milk canada")


def test_more_substring_regressions():
    # 'up' (from 'Laban Up') matched 'wp-content/uploads' in the old keyword scorer
    assert not phrase_in("up", url_path_text("https://alrawabi.ae/wp-content/uploads/2023/05/banner.jpg"))
    # 'lu' matched 'blue band' and every luluhypermarket URL
    assert not phrase_in("lu", "Blue Band Margarine 500g")
    assert not phrase_in("lu", url_path_text("https://www.luluhypermarket.com/en-ae/blue-band/p/1"))
    # 'al rawabi' is not 'al rabie' (no fuzzy matching)
    assert not phrase_in("al rawabi", "Al Rabie Juice 1L")
    # separators and case do not matter
    assert phrase_in("American Garden", "AMERICAN_GARDEN+Mayonnaise")
    assert phrase_in("mai dubai", "Mai-Dubai Water 500ml")


def test_arabic_folding_and_digits():
    assert normalize("إلمراعى") == normalize("المراعي")
    assert "180" in normalize("لبن ١٨٠ مل")
    assert normalize("حلـــيب") == normalize("حليب")          # tatweel stripped
    assert "ـ" not in normalize("حلـــيب")
    assert normalize("حَلِيبٌ") == "حليب"                        # harakat stripped
    assert normalize("مليحة") == normalize("مليحه")             # ta marbuta folded
    assert normalize("۱۲۳") == "123"                             # Persian digits
    assert normalize("  Almarai   FULL\tFat ") == "almarai full fat"
    slug = url_path_text("https://a.ae/masafi-water-1-5l")
    assert "1.5 l" in slug
    assert " 5 l" not in slug


def test_arabic_clitics():
    assert strip_arabic_clitics("المراعي") == "مراعي"
    assert strip_arabic_clitics("والمراعي") == "مراعي"
    assert strip_arabic_clitics("بالفراولة") == strip_arabic_clitics("فراولة")
    assert strip_arabic_clitics("لبن") == "لبن"                 # remainder would be < 3 chars
    assert strip_arabic_clitics("almarai") == "almarai"         # Latin untouched
    assert phrase_in("المراعي", "حليب والمراعي الطازج")
    assert phrase_in("الروابي", "لبن أب الروابي 180 مل")
    assert not phrase_in("المراعي", "حليب الروابي")


def test_url_path_text_drops_host_query_and_decodes():
    text = url_path_text("https://www.carrefouruae.com/mafuae/ar/%D8%A7%D9%84%D9%85%D8%B1%D8%A7%D8%B9%D9%8A-milk/p/9?utm=almarai")
    assert "carrefouruae" not in text
    assert "utm" not in text
    assert phrase_in("المراعي", text)
    assert url_path_text("https://x.ae/almarai-full-fat-2-85l/p/1").startswith("almarai full fat 2.85 l")
    # three-digit parts are not decimals ('6-330ml' is not 6.330 ml)
    assert "6.330" not in url_path_text("https://x.ae/pepsi-6-330ml")
    # filename_only ignores CDN directories
    assert url_path_text("https://cdn.x.com/images/products/almarai-milk-1l.jpg", filename_only=True) == "almarai milk 1l"
    assert url_path_text("") == ""
    assert url_path_text("https://cdn.x.com/") == ""


def test_tokens_and_hosts():
    assert tokens("7-UP 330ml") == ["7", "up", "330", "ml"]
    assert match_string("Al-Marai") == "al marai"
    assert url_host("https://WWW.Noon.com/uae-en/x") == "noon.com"
    assert url_host("carrefouruae.com/mafuae/en/x") == "carrefouruae.com"
    assert url_key("https://www.Shop.ae/img/A.jpg?w=300") == url_key("http://shop.ae/img/a.jpg")
