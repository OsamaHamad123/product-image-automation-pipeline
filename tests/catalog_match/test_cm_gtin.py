"""catalog_match.gtin: GS1 check digit and sheet-artefact handling (SRC-12, VL-11)."""

import socket

import pytest

from catalog_match.gtin import check_digit, gtin13, is_valid_gtin, normalize_gtin, same_gtin


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_cm_gtin():
    assert normalize_gtin("4006381333931") == ("04006381333931", "ok")      # EAN-13
    assert normalize_gtin("036000291452") == ("00036000291452", "ok")       # UPC-A
    gtin, status = normalize_gtin("96385074")                               # EAN-8
    assert status == "ok" and gtin == "00000096385074"
    assert normalize_gtin(" 4006381333931 ") == ("04006381333931", "ok")
    assert normalize_gtin("4006381333932") == (None, "bad_check_digit")
    assert normalize_gtin("6.29E+12") == (None, "scientific_notation")
    assert normalize_gtin("") == (None, "missing")


def test_gtin14_and_other_lengths():
    # GTIN-14 with indicator digit 1 over the EAN-13 above; check digit computed by hand:
    # 1 4 0 0 6 3 8 1 3 3 3 9 3 weighted 3,1,3,... from the right = 92 -> check digit 8
    assert normalize_gtin("14006381333938") == ("14006381333938", "ok")
    assert normalize_gtin("14006381333931")[1] == "bad_check_digit"
    assert normalize_gtin("12345")[1] == "bad_length"
    assert normalize_gtin("400638133393")[1] == "bad_check_digit"           # 12 digits, wrong check
    assert normalize_gtin("40063813339311")[1] == "bad_check_digit"


def test_sheet_artefacts():
    assert normalize_gtin("400-638-133-3931") == ("04006381333931", "ok")
    assert normalize_gtin("'4006381333931") == ("04006381333931", "ok")      # text-forced cell
    assert normalize_gtin("٤٠٠٦٣٨١٣٣٣٩٣١") == ("04006381333931", "ok")     # Eastern-Arabic digits
    assert normalize_gtin("6297000611365.0") == (None, "scientific_notation")
    assert normalize_gtin(6.297000611365e12) == (None, "scientific_notation")
    assert normalize_gtin(4006381333931) == ("04006381333931", "ok")        # an int keeps every digit
    assert normalize_gtin("6,297,000,611,365") == ("06297000611365", "ok")  # thousands separators
    assert normalize_gtin(None) == (None, "missing")
    assert normalize_gtin("   ") == (None, "missing")
    assert normalize_gtin("ABC123")[1] == "not_numeric"
    assert normalize_gtin("00000000")[1] == "all_zero"


def test_helpers():
    assert check_digit("400638133393") == 1
    assert is_valid_gtin("6297000611365")                                   # Mai Dubai, from the repo scripts
    assert not is_valid_gtin("6297000611366")
    assert gtin13("04006381333931") == "4006381333931"
    assert gtin13("14006381333938") == "14006381333938"
    assert same_gtin("4006381333931", "04006381333931") is True
    assert same_gtin("4006381333931", "6297000611365") is False
    assert same_gtin("4006381333931", "6.29E+12") is None
