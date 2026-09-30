"""catalog_match.abbreviations: sheet shorthand written out for the search engine (live run 2026-09-30).

Only query text is expanded; identity reads the same shorthand through the variant lexicon.
"""

import json
import socket

import pytest

from catalog_match import abbreviations
from catalog_match.abbreviations import expand, rules
from catalog_match.variants import extract_variants


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.mark.parametrize("name, expected", [
    ("ALALALI FANCY TUNA S/F OIL 85GM", "ALALALI FANCY TUNA SUNFLOWER OIL 85GM"),
    ("VIRGINIA L/MEAT TUNA WATER 170GM", "VIRGINIA LIGHT MEAT TUNA WATER 170GM"),
    ("SUP/T WT/MEAT SOLID TUNA 185GM", "SUP/T WHITE MEAT SOLID TUNA 185GM"),
    ("GOLDEN PRIZE TUNA VEG OIL 185GM", "GOLDEN PRIZE TUNA VEGETABLE OIL 185GM"),
    ("SUPER T SOLID TUNA SUNFL OIL 3X185GM", "SUPER T SOLID TUNA SUNFLOWER OIL 3X185GM"),
    ("RIO MARIE TUNA SUN OIL 3X70GM", "RIO MARIE TUNA SUNFLOWER OIL 3X70GM"),
    ("MUKALLA WHITE TUNA WITH VEG 185GM", "MUKALLA WHITE TUNA WITH VEGETABLES 185GM"),
    ("VIRGINIA LT/MEAT TUNA 170GM", "VIRGINIA LIGHT MEAT TUNA 170GM"),
    ("VIRGINIA W/MEAT TUNA 170GM", "VIRGINIA WHITE MEAT TUNA 170GM"),
])
def test_live_row_shorthand_is_written_out(name, expected):
    assert expand(name) == expected


def test_with_veg_is_vegetables_and_veg_oil_is_vegetable_oil():
    assert expand("MUKALLA WHITE TUNA WITH VEG 185GM") == "MUKALLA WHITE TUNA WITH VEGETABLES 185GM"
    assert extract_variants("MUKALLA WHITE TUNA WITH VEGETABLES 185GM", "tuna").get("medium") is None
    # The longer key wins: 'with veg oil' stays an oil.
    assert expand("TUNA WITH VEG OIL 185GM") == "TUNA WITH VEGETABLE OIL 185GM"
    assert expand("Tuna in veg. oil") == "Tuna in Vegetable Oil"
    # 'VEG' alone may be vegetable, vegetables or vegetarian: left as written.
    for name in ("MIXED VEG 400G", "VEG SAMOSA 12PCS", "VEG BIRYANI"):
        assert expand(name) == name


def test_longest_key_wins_whatever_the_file_order(tmp_path, monkeypatch):
    """The shipped file lists 'VEG OIL' first, which hides the rule; here the shorter key comes first."""
    data = tmp_path / "abbreviations.json"
    data.write_text(json.dumps({"groups": {
        "vegetables": {"why": "w", "expand": {"WITH VEG": "with Vegetables"}},
        "oil": {"why": "w", "expand": {"WITH VEG OIL": "with Vegetable Oil"}},
    }}), encoding="utf-8")
    original = abbreviations.ABBREVIATIONS_PATH
    try:
        monkeypatch.setattr(abbreviations, "ABBREVIATIONS_PATH", data)
        abbreviations.rules.cache_clear()
        abbreviations._vocabulary.cache_clear()
        assert expand("TUNA WITH VEG OIL 185GM") == "TUNA WITH VEGETABLE OIL 185GM"
        assert expand("TUNA WITH VEG 185GM") == "TUNA WITH VEGETABLES 185GM"
    finally:
        monkeypatch.setattr(abbreviations, "ABBREVIATIONS_PATH", original)
        abbreviations.rules.cache_clear()
        abbreviations._vocabulary.cache_clear()
    lengths = [len(rule.toks) for rule in rules()]
    assert lengths == sorted(lengths, reverse=True)


def test_context_bound_keys_need_canned_fish():
    assert expand("RIO MARIE TUNA SUN OIL 3X70GM") == "RIO MARIE TUNA SUNFLOWER OIL 3X70GM"
    assert expand("SARDINES IN SUN OIL 125G") == "SARDINES IN SUNFLOWER OIL 125G"
    # A sun-care oil and a luncheon meat keep their shorthand.
    assert expand("NIVEA SUN OIL SPF 30 200ML") == "NIVEA SUN OIL SPF 30 200ML"
    assert expand("ZWAN L/MEAT 340GM") == "ZWAN L/MEAT 340GM"
    # The context may come from outside the name (category, Arabic name).
    assert expand("XYZ SUN OIL 185G", "Canned Tuna") == "XYZ SUNFLOWER OIL 185G"
    assert expand("XYZ L/MEAT 185G", "تونة") == "XYZ LIGHT MEAT 185G"


def test_unknown_and_ambiguous_shorthand_stays_exactly_as_written():
    for name in (
        "LORENA TUNA CHUNK W/S 185GM",      # W/S: water & salt? white skipjack? ambiguous
        "JELLY S/F 85GM",                   # S/F without OIL: sugar free, salt free, sunflower
        "F/F MILK 1L", "L/F YOGHURT 500G",  # full fat / fat free, low fat / lactose free
        "BARTS TRADITON FRIES 1KG",         # a typo is not shorthand
        "ASHOKA PLAIN PARATHA 5S 400GM",    # '5S' is read by the size grammar
        "  Odd   spacing (kept) ; as-is ",
    ):
        assert expand(name) == name
    assert expand("") == "" and expand(None) == ""


def test_keys_match_whole_words_only():
    assert expand("TUNA W/S/F OIL") == "TUNA W/S/F OIL"          # 'S/F' is part of another abbreviation
    assert expand("TUNA S/FOIL") == "TUNA S/FOIL"
    assert expand("TUNA VEGOIL") == "TUNA VEGOIL"
    assert expand("TUNA (S/F) OIL") == "TUNA (S/F) OIL"          # a bracket ends a key
    assert expand("TUNA SUNFLOWER OIL") == "TUNA SUNFLOWER OIL"
    # Everything around a key is kept byte for byte; mixed case gets the file's words.
    assert expand(" ALALALI  TUNA S / F OIL, (85GM) ") == " ALALALI  TUNA SUNFLOWER OIL, (85GM) "
    assert expand("Chkn Bnls Breast 1kg") == "Chicken Boneless Breast 1kg"


def test_arabic_is_untouched():
    for text in ("تونة لحم أبيض بزيت دوار الشمس 185 جم", "تونة بالخضار", "حليب المراعي 1 لتر"):
        assert expand(text, "tuna") == text


def test_every_expansion_reads_as_the_same_variant():
    """Identity reads the shorthand through the lexicon; the written-out words must say the same."""
    assert rules()
    for rule in rules():
        ctx = "tuna" if rule.context is not None else None
        assert extract_variants(rule.key, ctx) == extract_variants(rule.words, ctx), rule
        # Written-out words are never shorthand themselves (expanding twice changes nothing).
        assert expand(rule.words, ctx) == rule.words, rule


def test_data_file_is_documented_and_checked(tmp_path, monkeypatch):
    raw = json.loads(abbreviations.ABBREVIATIONS_PATH.read_text(encoding="utf-8"))
    assert raw["_doc"]
    for name, group in raw["groups"].items():
        assert group.get("why", "").strip(), name
        assert group["expand"], name

    bad = tmp_path / "abbreviations.json"
    bad.write_text(json.dumps({"groups": {"x": {"why": "w", "context": "nope", "expand": {"A B": "C"}}}}),
                   encoding="utf-8")
    original = abbreviations.ABBREVIATIONS_PATH
    try:
        monkeypatch.setattr(abbreviations, "ABBREVIATIONS_PATH", bad)
        abbreviations.rules.cache_clear()
        abbreviations._vocabulary.cache_clear()
        with pytest.raises(ValueError, match="unknown context"):
            abbreviations.rules()
    finally:
        monkeypatch.setattr(abbreviations, "ABBREVIATIONS_PATH", original)
        abbreviations.rules.cache_clear()
        abbreviations._vocabulary.cache_clear()
    assert expand("TUNA S/F OIL") == "TUNA SUNFLOWER OIL"
