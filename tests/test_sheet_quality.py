"""«جودة بيانات الشيت»: what each sheet row lacks, computed where the dashboard already reads the sheet rows
(cli_bridge get_products, cached by the dashboard), so the Run page's card never adds a Google call.

Failed before the change: get_products said nothing about a row's gaps. Each product now carries sheet_issues: no
size, no (valid) barcode, a brand in no Brands Mapping entry, a likely typo with its suggested spelling (the sheet's
own words count: a word two names use is a word), and a barcode another, differently named product carries.
"""

import pytest


def _prod(row, name, brand, barcode="", size=""):
    return {"row_number": row, "product_name": name, "brand": brand, "barcode": barcode, "size": size,
            "product_name_ar": "", "brand_ar": "", "category": "", "existing_image_link": ""}


SHEET = [
    _prod(2, "MR JOHN FRENCH FRIES", "MR JOHN"),
    _prod(3, "TARGET CHICEKN LUNCHEON MEAT 340GM", "TARGET", barcode="6.29E+12"),
    _prod(4, "ALMARAI FRESH MILK 1L", "ALMARAI", barcode="6281007035309"),
    _prod(5, "ALMARAI FRESH LABAN 1L", "ALMARAI", barcode="6281007035309"),
    _prod(6, "ALMARAI FRESH MILK 2L", "ALMARAI", barcode="6281007035316"),
    _prod(7, "A MENUDO SAUCE 200G", "A", barcode="6281007035323"),
    _prod(8, "B MENUDO STEW 200G", "B", barcode="6281007035330"),
    _prod(9, "C MENDUO MIX 200G", "C", barcode="6281007035347"),
]
MAPPINGS = {k: {"brand": k.upper(), "synonyms": [k.upper()]} for k in ("mr john", "almarai", "a", "b", "c")}


@pytest.fixture
def bridge(mariadb_or_skip, monkeypatch, tmp_path):
    import cli_bridge
    import google_sheets
    from catalog_match import explain

    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: object())
    monkeypatch.setattr(google_sheets, "get_products", lambda ws: ([dict(p) for p in SHEET], 9))
    monkeypatch.setattr(cli_bridge, "_load_brand_mappings", lambda: MAPPINGS)
    monkeypatch.setattr(explain, "PRODUCTS_CACHE", tmp_path / "products_cache.json")
    return cli_bridge


def test_every_product_carries_what_its_sheet_row_lacks(bridge):
    out = bridge.action_get_products({})
    issues = {p["row_number"]: {i["key"]: i for i in p["sheet_issues"]} for p in out["products"]}
    assert set(issues[2]) == {"no_size", "no_barcode"}                       # mapped brand: not unknown
    assert issues[2]["no_size"]["text"] == "الحجم ناقص بالشيت"
    row3 = issues[3]
    assert row3["no_barcode"]["status"] == "scientific_notation"
    assert row3["brand_unknown"]["text"] == "الماركة «TARGET» مش موجودة في Brands Mapping"
    assert (row3["typo"]["word"], row3["typo"]["suggest"], row3["typo"]["known"]) == ("CHICEKN", "CHICKEN", False)
    assert issues[4]["duplicate_barcode"]["rows"] == [5] and issues[5]["duplicate_barcode"]["rows"] == [4]
    assert issues[4]["duplicate_barcode"]["text"] == "نفس الباركود مكتوب لمنتج ثاني (صف 5)"
    assert issues[6] == {} and issues[7] == {} and issues[8] == {}
    # 'MENUDO' is a word two names of this sheet use; a single-use neighbour of it is a likely typo
    assert (issues[9]["typo"]["word"], issues[9]["typo"]["suggest"]) == ("MENDUO", "MENUDO")
    # the products keep everything else they carried (sku_key, sheet_states, failures)
    assert all(p["sku_key"] and "sheet_states" in p and "has_error" in p for p in out["products"])


def test_the_brand_column_and_a_size_in_mm_get_their_own_notes(bridge, monkeypatch):
    # live run 2026-10-04 (every brand unmapped): rows 4, 28, 79 and 95 as the sheet has them
    import google_sheets

    rows = [_prod(4, "BATO FRENCH FRIES 900 MM", "BATO"),
            _prod(28, "AMERICAN LIGHT MEAT TUNA SOLID 185GM", "AMERICAN LIGHT"),
            _prod(79, "SQ SALITED DRY PRAWNS FISF", "SQ SALITED"),
            _prod(95, "ICE CREAM CANDY 13 GM", "GENERIC / NO BRAND"),
            _prod(5, "FARMILA FRENCH FRIES 9MM 1KG", "FARMILA")]
    monkeypatch.setattr(google_sheets, "get_products", lambda ws: ([dict(p) for p in rows], 5))
    out = bridge.action_get_products({})
    issues = {p["row_number"]: {i["key"]: i for i in p["sheet_issues"]} for p in out["products"]}
    assert issues[4]["size_unit_typo"]["text"] == "الحجم مكتوب «900 MM» — غالبًا قصدك «900 GM»"
    assert issues[28]["brand_has_product_word"]["text"] == \
        "عمود الماركة فيه كلمة من اسم المنتج: «AMERICAN LIGHT» — الماركة غالبًا «AMERICAN»"
    assert issues[79]["brand_has_product_word"]["text"] == \
        "عمود الماركة فيه كلمة من اسم المنتج: «SQ SALITED» — الماركة غالبًا «SQ»، و«SALITED» قصدك «SALTED»"
    assert "no_brand" in issues[95] and "brand_unknown" not in issues[95]     # was 'add GENERIC / NO BRAND'
    assert set(issues[5]) == {"no_barcode", "brand_unknown"}                  # a 9 mm cut with its 1 KG: no note


def test_a_failing_check_never_hides_the_products(bridge, monkeypatch):
    from catalog_match import explain

    def boom(*args, **kwargs):
        raise RuntimeError("lexicon unreadable")

    monkeypatch.setattr(explain, "sheet_issues", boom)
    out = bridge.action_get_products({})
    assert out["status"] == "success" and len(out["products"]) == len(SHEET)
    assert all(p["sheet_issues"] is None and p["sku_key"] for p in out["products"])
