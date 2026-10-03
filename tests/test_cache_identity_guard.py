"""A cached approved image found by barcode is served only to the same product (phase 3, identity).

Barcodes in the owner's sheet may be missing, wrong or shared by two rows. The identity is brand +
product name (+ size / variant); a barcode-keyed cache row is served only when its brand matches
the requested brand (normalised, Brands Mapping synonyms) and the product names agree (same product
words, size and variant). Otherwise the cache is ignored and the product is searched.
"""

import pytest

SHARED = "6281007000024"
MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["المراعي", "Al Marai"]}}


HASH_KEY = "abcdef0123456789"


def _clear(db):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM resolved_products WHERE barcode = %s OR sku_key IN (%s, %s)",
                        (SHARED, "0" + SHARED, HASH_KEY))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip
    _clear(db)
    yield db
    _clear(db)


def approve(db, name, brand, url, barcode=SHARED, sku_key="0" + SHARED):
    assert db.save_product_resolution(barcode, name, brand, f"https://src/{url}.jpg", f"https://res/{url}.png",
                                      verification_status="human_approved", approved_by="human", sku_key=sku_key)


# ---------------------------------------------------------------------------
# get_cached_product against MariaDB
# ---------------------------------------------------------------------------

def test_a_shared_barcode_never_hands_one_product_the_others_image(db):
    approve(db, "BARTS PEANUT BUTTER SMOOTH 340G", "BARTS", "barts-pb")
    # the other product that carries the same barcode in the sheet
    for kwargs in (dict(barcode=SHARED, product_name="BARTS STRAWBERRY JAM 450G", brand="BARTS"),
                   dict(barcode=SHARED, product_name="BARTS STRAWBERRY JAM 450G", brand="BARTS",
                        sku_key="0" + SHARED),
                   dict(barcode=SHARED, product_name="BARTS PEANUT BUTTER CRUNCHY 340G", brand="BARTS"),
                   dict(barcode=SHARED, product_name="BARTS PEANUT BUTTER SMOOTH 1KG", brand="BARTS"),
                   dict(barcode=SHARED, product_name="EMBORG PEANUT BUTTER SMOOTH 340G", brand="EMBORG")):
        assert db.get_cached_product(**kwargs) is None, kwargs
    # the product that was approved still gets its image
    hit = db.get_cached_product(barcode=SHARED, product_name="BARTS PEANUT BUTTER SMOOTH 340G", brand="BARTS",
                                sku_key="0" + SHARED)
    assert hit is not None and hit["cloudinary_url"] == "https://res/barts-pb.png"


def test_brand_synonyms_and_spelling_still_match(db):
    approve(db, "Almarai Full Fat Milk 1L", "Almarai", "almarai-ff")
    for brand in ("ALMARAI", "Al-Marai", "Al Marai"):
        hit = db.get_cached_product(barcode=SHARED, product_name="ALMARAI FULL FAT MILK 1L", brand=brand)
        assert hit is not None, brand
    # the Arabic brand needs the Brands Mapping sheet to be the same brand
    assert db.get_cached_product(barcode=SHARED, product_name="Almarai Full Fat Milk 1L", brand="المراعي") is None
    hit = db.get_cached_product(barcode=SHARED, product_name="Almarai Full Fat Milk 1L", brand="المراعي",
                                brand_mappings=MAPPINGS)
    assert hit is not None and hit["cloudinary_url"] == "https://res/almarai-ff.png"


def test_variant_and_size_differences_are_other_products(db):
    approve(db, "Almarai Full Fat Milk 1L", "Almarai", "almarai-ff")
    for name in ("Almarai Low Fat Milk 1L", "Almarai Full Fat Milk 2L", "Almarai Full Fat Milk 6 x 1L",
                 "Almarai Full Fat Milk", "Almarai Laban 1L"):
        assert db.get_cached_product(barcode=SHARED, product_name=name, brand="Almarai") is None, name


def test_the_sheet_size_column_takes_part_in_the_check(db):
    # Review fix: the sheet's SIZE column is part of the product. Two rows that share a barcode and
    # a name ('Almarai Fresh Milk') but not the size column (1L / 2L) are two products; the stored
    # row keeps only the name, so a size that only the column states cannot be confirmed.
    approve(db, "Almarai Fresh Milk", "Almarai", "no-size-in-name")
    for size in ("2L", "1L"):
        assert db.get_cached_product(barcode=SHARED, product_name="Almarai Fresh Milk", brand="Almarai",
                                     size_text=size) is None, size
    # without a size column the names agree as before
    assert db.get_cached_product(barcode=SHARED, product_name="Almarai Fresh Milk", brand="Almarai") is not None
    _clear(db)
    approve(db, "Almarai Fresh Milk 1L", "Almarai", "size-in-name")
    hit = db.get_cached_product(barcode=SHARED, product_name="Almarai Fresh Milk", brand="Almarai", size_text="1L")
    assert hit is not None and hit["cloudinary_url"] == "https://res/size-in-name.png"
    assert db.get_cached_product(barcode=SHARED, product_name="Almarai Fresh Milk", brand="Almarai",
                                 size_text="2 L") is None


def test_v2_search_checks_the_size_column(db, monkeypatch):
    import image_search
    from catalog_match import pipeline
    from catalog_match.models import SearchOutcome

    approve(db, "Almarai Fresh Milk 1L", "Almarai", "size-in-name")
    searched = []

    def fake_find(spec, **kwargs):
        searched.append((spec.raw_name, spec.size.canonical() if spec.size else None))
        return SearchOutcome(decision="NOT_FOUND", failure_code="NO_RESULTS", sku_key=spec.sku_key)

    monkeypatch.setattr(pipeline, "find_product_image", fake_find)
    monkeypatch.setattr(image_search, "_load_brand_mappings_for_search", lambda: {})
    out = image_search.search_best_product_image_v2("q", "Almarai Fresh Milk", "Almarai", barcode=SHARED,
                                                    size_text="2L")
    assert out is None and len(searched) == 1
    out = image_search.search_best_product_image_v2("q", "Almarai Fresh Milk", "Almarai", barcode=SHARED,
                                                    size_text="1L")
    assert out is not None and out["url"] == "https://res/size-in-name.png" and len(searched) == 1


def test_a_hash_sku_key_hit_is_unchanged(db):
    # a non-barcode sku_key already encodes brand + name + size: no extra check
    approve(db, "Almarai Full Fat Milk 1L", "Almarai", "hash-key", barcode="", sku_key=HASH_KEY)
    hit = db.get_cached_product(product_name="Almarai Full Fat Milk 1L", brand="Almarai", sku_key=HASH_KEY)
    assert hit is not None and hit["cloudinary_url"] == "https://res/hash-key.png"


# ---------------------------------------------------------------------------
# The v2 search entry point: a mismatching cache row means a real search
# ---------------------------------------------------------------------------

def test_v2_search_ignores_another_products_cached_image(db, monkeypatch):
    import image_search
    from catalog_match import pipeline
    from catalog_match.models import SearchOutcome

    approve(db, "BARTS PEANUT BUTTER SMOOTH 340G", "BARTS", "barts-pb")
    searched = []

    def fake_find(spec, **kwargs):
        searched.append(spec.raw_name)
        return SearchOutcome(decision="NOT_FOUND", failure_code="NO_RESULTS", sku_key=spec.sku_key)

    monkeypatch.setattr(pipeline, "find_product_image", fake_find)
    monkeypatch.setattr(image_search, "_load_brand_mappings_for_search", lambda: {})
    out = image_search.search_best_product_image_v2("q", "BARTS STRAWBERRY JAM 450G", "BARTS", barcode=SHARED)
    assert out is None and searched == ["BARTS STRAWBERRY JAM 450G"]
    # the approved product itself is still a cache hit (no search)
    trace = {}
    out = image_search.search_best_product_image_v2("q", "BARTS PEANUT BUTTER SMOOTH 340G", "BARTS", barcode=SHARED,
                                                    trace=trace)
    assert out is not None and out["url"] == "https://res/barts-pb.png" and out["source"] == "sqlite_cache"
    assert searched == ["BARTS STRAWBERRY JAM 450G"] and trace["outcome"]["cache_hit"] is True


def test_v2_search_passes_the_brand_mappings_to_the_cache_check(db, monkeypatch):
    # the sheet row writes the brand in Arabic; only the Brands Mapping sheet says it is Almarai
    import image_search
    from catalog_match import pipeline
    from catalog_match.models import SearchOutcome

    approve(db, "Almarai Full Fat Milk 1L", "Almarai", "almarai-ff")
    searched = []

    def fake_find(spec, **kwargs):
        searched.append(spec.raw_name)
        return SearchOutcome(decision="NOT_FOUND", failure_code="NO_RESULTS", sku_key=spec.sku_key)

    monkeypatch.setattr(pipeline, "find_product_image", fake_find)
    monkeypatch.setattr(image_search, "_load_brand_mappings_for_search", lambda: {})
    out = image_search.search_best_product_image_v2("q", "Almarai Full Fat Milk 1L", "المراعي", barcode=SHARED,
                                                    brand_mappings=MAPPINGS)
    assert out is not None and out["url"] == "https://res/almarai-ff.png" and searched == []
    # without the mappings the two brand cells cannot be shown to agree: searched, never served
    out = image_search.search_best_product_image_v2("q", "Almarai Full Fat Milk 1L", "المراعي", barcode=SHARED)
    assert out is None and searched == ["Almarai Full Fat Milk 1L"]


# ---------------------------------------------------------------------------
# cached_row_matches: the comparison itself (no database)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stored_name, stored_brand, name, brand, same", [
    ("Almarai Full Fat Milk 1L", "Almarai", "ALMARAI FULL FAT MILK 1L", "ALMARAI", True),
    ("Almarai Full Fat Milk 1L", "Almarai", "Almarai Full Fat Milk 1 Litre", "Almarai", True),
    ("Almarai Full Fat Fresh Milk 1L", "Almarai", "Almarai Full Fat Fresh Milk 1000ml", "Almarai", True),
    ("Almarai Full Fat Milk 1L", "", "Almarai Full Fat Milk 1L", "Almarai", True),       # brand named in the name
    ("Full Fat Milk 1L", "", "Full Fat Milk 1L", "Almarai", False),                     # brand nowhere
    ("Almarai Full Fat Milk 1L", "Almarai", "Almarai Full Fat Milk 1L", "Nada", False),
    ("Pepsi Diet 330ml", "Pepsi", "Pepsi 330ml", "Pepsi", False),                       # a marked variant
    ("Pepsi 330ml", "Pepsi", "Pepsi 330ml x 6", "Pepsi", False),                        # a pack
    ("BARTS PEANUT BUTTER SMOOTH 340G", "BARTS", "BARTS PEANUT BUTTER CRUNCHY 340G", "BARTS", False),
    ("BARTS PEANUT BUTTER 340G", "BARTS", "BARTS STRAWBERRY JAM 340G", "BARTS", False),
    ("", "BARTS", "BARTS PEANUT BUTTER 340G", "BARTS", False),                          # nothing stored
    ("", "Almarai", "Almarai", "Almarai", False),                                       # no stored name at all
])
def test_cached_row_matches(offline, stored_name, stored_brand, name, brand, same):
    import local_cache_db

    row = {"product_name": stored_name, "brand": stored_brand}
    assert local_cache_db.cached_row_matches(row, name, brand) is same


def test_live_rows_never_share_a_cached_image(offline):
    # The owner's 60 live sheet rows (abbreviations, glued brands, 'S/F OIL' vs 'WATER', 'L/MEAT' vs
    # 'WHITE'): if every pair shared a barcode, only the true duplicate (rows 54 / 55, '160 GM' vs
    # '160GM') may be served the other's cached image, and every row its own.
    import itertools
    import json
    from pathlib import Path

    import local_cache_db

    path = Path(__file__).parent / "catalog_match" / "fixtures" / "live_rows_2026_09_30.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    served = {(a["row"], b["row"]) for a, b in itertools.permutations(rows, 2)
              if local_cache_db.cached_row_matches({"product_name": a["name"], "brand": a["brand"]},
                                                   b["name"], b["brand"])}
    assert served == {(54, 55), (55, 54)}
    for r in rows:
        assert local_cache_db.cached_row_matches({"product_name": r["name"], "brand": r["brand"]},
                                                 r["name"], r["brand"]), r


def test_cached_row_matches_fails_closed(offline, monkeypatch):
    import local_cache_db
    from catalog_match import identity

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(identity, "build_sku_spec", broken)
    assert local_cache_db.cached_row_matches({"product_name": "Almarai Milk 1L", "brand": "Almarai"},
                                             "Almarai Milk 1L", "Almarai") is False
