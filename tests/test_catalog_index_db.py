"""catalog_match.local_index.DbCatalogStore against MariaDB (`automation_test`): the local catalog index tables.

catalog_products / catalog_tokens / catalog_harvests come from local_cache_db.init_db(). The search
logic is covered with MemoryCatalogStore in tests/catalog_match/test_cm_local_index.py; these tests
pin what only the database can get wrong: upserts, the brand-word query, the GTIN lookup, page
records with their freshness, pruning (tokens go with their row) and the statistics.
"""

import datetime as dt

import pytest

from catalog_match.local_index import DbCatalogStore, PageRecord, index_keys

LULU = "https://gcc.luluhypermarket.com/en-ae/{}/p/{}"
CARREFOUR = "https://www.carrefouruae.com/mafuae/en/frozen-bread/{}/p/{}"


def _wipe(db):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM catalog_products")
            cur.execute("DELETE FROM catalog_harvests")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def store(mariadb_or_skip):
    db = mariadb_or_skip
    _wipe(db)
    DbCatalogStore._count_cache.clear()
    yield DbCatalogStore()
    _wipe(db)
    DbCatalogStore._count_cache.clear()      # other tests must not see a cached non-empty index


def _sql(db, sql, params=()):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def test_upsert_counts_new_pages_once_and_indexes_their_slug_words(store, mariadb_or_skip):
    urls = [(LULU.format("ashoka-plain-paratha-400-g", 1), "2026-09-01"),
            (LULU.format("ashoka-garlic-naan-400-g", 2), None),
            (CARREFOUR.format("kawan-plain-paratha-400g", 3), None)]
    assert store.upsert("lulu", urls[:2]) == 2
    assert store.upsert("carrefour_uae", urls[2:]) == 1
    assert store.upsert("lulu", [(urls[0][0] + "?srsltid=x", "2026-09-30")]) == 0     # the same page
    assert store.count(fresh=True) == 3
    rows = _sql(mariadb_or_skip, "SELECT url, lastmod, slug_text FROM catalog_products ORDER BY id")
    assert rows[0] == {"url": urls[0][0], "lastmod": "2026-09-30", "slug_text": "ashoka plain paratha 400 g"}
    tokens = {r["token"] for r in _sql(mariadb_or_skip, "SELECT token FROM catalog_tokens WHERE product_id = "
                                                        "(SELECT id FROM catalog_products WHERE url = %s)", (urls[0][0],))}
    assert tokens == set(index_keys("ashoka plain paratha 400 g"))


def test_find_needs_every_brand_word_and_ranks_by_product_words(store):
    store.upsert("lulu", [(LULU.format("ashoka-garlic-naan-400-g", 1), None),
                          (LULU.format("ashoka-plain-paratha-400-g", 2), None),
                          (LULU.format("kawan-plain-paratha-400-g", 3), None),
                          (LULU.format("al-alali-fancy-meat-tuna-170-g", 4), None)])
    rows = store.find(["ashoka"], ["plain", "paratha"])
    assert [r.slug_text for r in rows] == ["ashoka plain paratha 400 g", "ashoka garlic naan 400 g"]
    assert [r.hits for r in rows] == [3, 1] and all(r.page_age_h is None and r.page_status == "" for r in rows)
    assert [r.slug_text for r in store.find(["al", "alali"], ["tuna"])] == ["al alali fancy meat tuna 170 g"]
    assert store.find(["nothing"], ["paratha"]) == [] and store.find([], ["paratha"]) == []
    assert len(store.find(["ashoka"], [], limit=1)) == 1


def test_a_page_record_is_kept_with_its_age_and_found_by_gtin(store):
    store.upsert("lulu", [(LULU.format("plain-paratha-400-g", 1), None)])        # slug without the brand
    (row,) = store.find(["plain"], [])
    store.save_page(row.id, PageRecord(status="ok", page_title="Ashoka Plain Paratha 400 g",
                                       image_url="https://gcc.luluhypermarket.com/medias/1.jpg", width=900,
                                       height=900, gtin="08906008560022"))
    (by_gtin,) = store.by_gtin("08906008560022")
    assert (by_gtin.page_status, by_gtin.page_title, by_gtin.image_url, by_gtin.image_width, by_gtin.page_age_h) == \
        ("ok", "Ashoka Plain Paratha 400 g", "https://gcc.luluhypermarket.com/medias/1.jpg", 900, 0)
    # the page title's words are searchable now: the brand the slug left out finds the row
    assert [r.id for r in store.find(["ashoka"], ["paratha"])] == [row.id]
    store.save_page(row.id, PageRecord(status="timeout"))
    (again,) = store.find(["ashoka"], [])
    # a transient failure changes only the status: the image, size and GTIN an earlier read found stay
    assert (again.page_status, again.image_url, again.image_width, again.gtin, again.page_title) == \
        ("timeout", "https://gcc.luluhypermarket.com/medias/1.jpg", 900, "08906008560022", "Ashoka Plain Paratha 400 g")
    assert [r.id for r in store.by_gtin("08906008560022")] == [row.id]
    store.save_page(row.id, PageRecord(status="http_404"))          # a permanent answer replaces it
    (gone,) = store.find(["ashoka"], [])
    assert (gone.page_status, gone.image_url, gone.gtin) == ("http_404", "", None)


def test_prune_removes_pages_no_longer_listed_with_their_words(store, mariadb_or_skip):
    old, kept = LULU.format("ashoka-old-pack-400-g", 1), LULU.format("ashoka-plain-paratha-400-g", 2)
    store.upsert("lulu", [(old, None), (kept, None)])
    store.upsert("carrefour_uae", [(CARREFOUR.format("ashoka-plain-paratha-400g", 3), None)])
    _sql(mariadb_or_skip, "UPDATE catalog_products SET last_seen = NOW() - INTERVAL 2 DAY")
    started = store.begin_harvest("lulu")
    assert isinstance(started, dt.datetime)
    store.upsert("lulu", [(kept, None)])
    assert store.prune("lulu", started) == 1
    assert sorted(r.slug_text for r in store.find(["ashoka"], [])) == ["ashoka plain paratha 400 g",
                                                                     "ashoka plain paratha 400g"]
    assert _sql(mariadb_or_skip, "SELECT COUNT(*) AS n FROM catalog_tokens t LEFT JOIN catalog_products p "
                                 "ON p.id = t.product_id WHERE p.id IS NULL")[0]["n"] == 0


def test_stats_per_store_and_the_last_harvest(store):
    started = store.begin_harvest("lulu")
    store.upsert("lulu", [(LULU.format("ashoka-plain-paratha-400-g", 1), None),
                          (LULU.format("ashoka-garlic-naan-400-g", 2), None)])
    (row,) = store.find(["garlic"], [])
    store.save_page(row.id, PageRecord(status="ok", image_url="https://x/1.jpg"))
    store.finish_harvest("lulu", started, {"status": "ok", "sitemaps_read": 3, "urls_seen": 9, "product_urls": 2,
                                           "new_urls": 2})
    (lulu,) = store.stats()
    assert {k: lulu[k] for k in ("store", "products", "pages_read", "with_image", "last_status")} == \
        {"store": "lulu", "products": 2, "pages_read": 1, "with_image": 1, "last_status": "ok"}
    assert lulu["last_harvest"]


def test_the_row_count_is_cached_and_an_upsert_refreshes_it(store):
    assert store.count() == 0
    store.upsert("lulu", [(LULU.format("ashoka-plain-paratha-400-g", 1), None)])
    assert store.count() == 1
