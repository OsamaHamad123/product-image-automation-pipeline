"""Learning from review decisions, on the real MariaDB test database (skips when it is down).

An approval of a pick that carried 'brand_spelling:<spelling>' records the spelling for the sheet brand
(cli_bridge._learn_brand_spelling -> local_cache_db.record_brand_alias); a WRONG_BRAND rejection counts
against it; the sites reviewers keep approving a brand's images from come out of review_decisions; and
google_sheets.get_brand_mappings hands both to every search (catalog_match.learning.load_and_apply).
"""

import pytest

from catalog_match import learning
from catalog_match.identity import build_sku_spec

BRANDS = ("SUP/T", "SUPER T/", "KABANI", "TESTLEARN")


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _wipe(db):
    marks = ",".join(["%s"] * len(BRANDS))
    _sql(db, f"DELETE FROM learned_brand_aliases WHERE sheet_brand IN ({marks})", BRANDS)
    _sql(db, f"DELETE FROM review_decisions WHERE brand IN ({marks})", BRANDS)
    learning.clear_cache()


@pytest.fixture
def db(mariadb_or_skip):
    _wipe(mariadb_or_skip)
    yield mariadb_or_skip
    _wipe(mariadb_or_skip)


def test_approvals_teach_a_spelling_and_wrong_brand_rejections_unteach_it(db):
    assert db.record_brand_alias("SUP/T", "Super Tasty")
    assert db.record_brand_alias("SUP/T", "Super Tasty")
    assert not db.record_brand_alias("SUP/T", "sup t")                  # the same spelling: nothing to learn
    assert ("SUP/T", "Super Tasty", 2, 0) in db.get_learned_brand_aliases()
    db.record_brand_alias("SUP/T", "Super Tasty", approved=False)
    db.record_brand_alias("SUP/T", "Super Tasty", approved=False)
    assert not [a for a in db.get_learned_brand_aliases() if a[0] == "SUP/T"]   # 2 approvals, 2 rejections


def _kabani_sources(db):
    return learning.apply({}, [], db.get_brand_source_counts()).get("learned source: kabani", {}).get("learned_domains")


def test_sources_need_two_approved_products_and_no_identity_rejection(db):
    for sku in ("k1", "k2"):
        db.add_review_decision("approved", sku_key=sku, brand="KABANI", page_domain="sharjahcoop.ae")
        db.add_review_decision("approved", sku_key=sku, brand="KABANI", page_domain="www.example-grocer.com")
    db.add_review_decision("approved", sku_key="k1", brand="KABANI", page_domain="tradeling.com")   # only once
    db.add_review_decision("rejected", brand="KABANI", page_domain="www.example-grocer.com", reason_code="WRONG_SIZE")
    db.add_review_decision("rejected", brand="KABANI", page_domain="sharjahcoop.ae", reason_code="LOW_QUALITY")
    rows = {(b, d): (ok, bad) for b, d, ok, bad in db.get_brand_source_counts() if b == "KABANI"}
    assert rows[("KABANI", "sharjahcoop.ae")] == (2, 0) and rows[("KABANI", "www.example-grocer.com")] == (2, 1)
    assert _kabani_sources(db) == ["sharjahcoop.ae"]


def test_one_product_approved_again_is_one_approval(db):
    for _ in range(3):      # a re-approval or a retry of the same product
        db.add_review_decision("approved", sku_key="k1", brand="KABANI", product_name="KABANI MEAT MASALA 160 GM",
                               page_domain="sharjahcoop.ae")
    assert _kabani_sources(db) is None
    db.add_review_decision("approved", product_name="KABANI CHICKEN MASALA 160 GM", page_domain="sharjahcoop.ae",
                           brand="KABANI")                 # a second product (no sku_key: its name counts)
    assert _kabani_sources(db) == ["sharjahcoop.ae"]


def test_the_bridge_learns_from_the_stored_reasons_or_the_screens_warnings(db):
    import cli_bridge

    stored = {"reasons": ["tier T2", "warn:brand_spelling:Super Tasty", "warn:vlm_unsure"]}
    cli_bridge._learn_brand_spelling("approved", {"brand": "SUP/T"}, stored, None)
    cli_bridge._learn_brand_spelling("approved", {"brand": "SUPER T/",
                                                  "candidate_warnings": "vlm_unsure|brand_spelling:Super Tasty"},
                                     {"identity_tier": "2"}, None)
    cli_bridge._learn_brand_spelling("rejected", {"brand": "SUP/T"}, stored, "WRONG_SIZE")     # not about the brand
    cli_bridge._learn_brand_spelling("manual_upload", {"brand": "SUP/T"}, stored, None)
    cli_bridge._learn_brand_spelling("approved", {"brand": "TESTLEARN"}, {"reasons": ["warn:vlm_unsure"]}, None)
    rows = {(r[0], r[1]): r[2:] for r in db.get_learned_brand_aliases()}
    assert rows[("SUP/T", "Super Tasty")] == (1, 0) and rows[("SUPER T/", "Super Tasty")] == (1, 0)
    assert not [k for k in rows if k[0] == "TESTLEARN"]
    cli_bridge._learn_brand_spelling("rejected", {"brand": "SUP/T"}, stored, "WRONG_BRAND")
    assert ("SUP/T", "Super Tasty") not in {(r[0], r[1]) for r in db.get_learned_brand_aliases()}


def test_every_search_gets_what_was_learned_through_get_brand_mappings(db, monkeypatch):
    import google_sheets

    db.record_brand_alias("SUPER T/", "Super Tasty")
    for sku in ("st1", "st2"):
        db.add_review_decision("approved", sku_key=sku, brand="SUPER T/", page_domain="www.tradeling.com")
    monkeypatch.setattr(google_sheets, "_sheet_brand_mappings", lambda client, sheet: {})
    learning.clear_cache()
    mappings = google_sheets.get_brand_mappings(None, "sheet")
    spec = build_sku_spec({"name": "SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "brand": "SUPER T/"}, mappings)
    assert (spec.brand_conf, spec.brand_canonical, spec.learned_domains) == ("learned", "Super Tasty",
                                                                          ("tradeling.com",))


def test_without_a_database_the_sheet_mappings_come_back_unchanged(monkeypatch):
    import local_cache_db

    def down():
        raise OSError("database is down")

    monkeypatch.setattr(local_cache_db, "get_learned_brand_aliases", down)
    learning.clear_cache()
    sheet = {"almarai": {"brand": "Almarai", "synonyms": ["Almarai"]}}
    assert learning.load_and_apply(sheet) == sheet
    learning.clear_cache()
