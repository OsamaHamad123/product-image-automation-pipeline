"""«ماركات ناقصة»: the Brands Mapping assistant (catalog_match/brand_assistant.py and cli_bridge brand_* actions).

- brand_suggestions lists the queue's brands that have no sheet entry (never a placeholder or a mapped brand), with the
  row count, the Arabic name and the store spellings the search discovered or learned; it reads only: no write, no paid
  call, and a sheet that cannot be read is an error, not «every brand is missing»;
- brand_official_site sends exactly ONE Serper web query, records it in the spend ledger, and proposes up to two sites
  that are not a retailer, marketplace, social network or stock site and name the brand; it writes nothing;
- brand_add appends one row per brand (the right columns, one write) after a fresh read of the sheet, refuses a
  duplicate, a bad domain, a retailer's domain and more than ten synonyms before anything is written, drops the brand
  cache and queues the official site for indexing at the next worker run (harvest_pending).
"""

import json
import re

import pytest

from catalog_match import brand_assistant as ba

HEADERS = ["Brand", "Synonyms", "Excluded Competitors", "Sub-brands", "Official domains"]


# ---------------------------------------------------------------------------
# the official site picker
# ---------------------------------------------------------------------------

RESULTS = [
    {"link": "https://www.noon.com/uae-en/almarai-milk/p", "title": "Almarai Milk | noon"},
    {"link": "https://www.facebook.com/almarai", "title": "Almarai - Home | Facebook"},
    {"link": "https://www.instagram.com/almarai/", "title": "Almarai (@almarai) Instagram"},
    {"link": "https://www.shutterstock.com/search/almarai", "title": "Almarai stock photos"},
    {"link": "https://en.wikipedia.org/wiki/Almarai", "title": "Almarai - Wikipedia"},
    {"link": "https://www.carrefouruae.com/mafuae/en/c/almarai", "title": "Almarai at Carrefour"},
    {"link": "https://www.almarai.com/en/", "title": "Almarai | Official website"},
    {"link": "https://www.almarai.com/en/products", "title": "Almarai products"},
    {"link": "https://news.example.org/almarai-profits", "title": "Almarai profits rise again"},
]


def test_the_picker_skips_retailers_social_and_stock_sites_and_keeps_the_brands_own_site():
    picked = ba.official_site_candidates(RESULTS, "Almarai")
    assert [c["domain"] for c in picked] == ["almarai.com", "news.example.org"]       # up to two, one per domain
    assert picked[0] == {"title": "Almarai | Official website", "domain": "almarai.com",
                         "url": "https://www.almarai.com/en/"}
    only_stores = [r for r in RESULTS if "almarai.com" not in r["link"] and "example.org" not in r["link"]]
    assert ba.official_site_candidates(only_stores, "Almarai") == []


def test_the_picker_needs_the_brands_main_word_in_the_host_or_the_title():
    results = [{"link": "https://www.unrelated-site.com/", "title": "Dairy products"},
               {"link": "https://sabasanabel.com/", "title": "Flour and rice"},
               {"link": "https://blog.example.com/post", "title": "Why Sanabel rice is popular"}]
    assert [c["domain"] for c in ba.official_site_candidates(results, "Saba Sanabel")] == ["sabasanabel.com"]
    assert ba.official_site_candidates([{"link": "https://www.unrelated-site.com/", "title": "Dairy"}], "Meliha") == []
    assert ba.main_token("Al Rawabi") == "rawabi" and ba.main_token("") == ""


@pytest.mark.parametrize("host", ["noon.com", "www.carrefouruae.com", "carrefour.com", "amazon.ae", "facebook.com",
                                  "m.facebook.com", "x.com", "shutterstock.com", "youtube.com", "tiktok.com"])
def test_stores_social_networks_and_stock_sites_are_blocked(host):
    assert ba.blocked_site(host)


@pytest.mark.parametrize("host", ["almarai.com", "sabasanabel.com", "targetfoods.ae", "meliha.ae"])
def test_a_brands_own_site_is_not_blocked(host):
    assert not ba.blocked_site(host)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["https://almarai.com", "almarai.com/en", "almarai.com:8080", "almarai", "al marai.com",
                                   "http://a.ae", "a..com", "-a.com", "a.c", "", "192.168.0.1"])
def test_a_domain_must_be_a_bare_host(value):
    assert ba.clean_domain(value) == ""
    if value:
        with pytest.raises(ba.BrandRequestError) as err:
            ba.clean_domains([value])
        assert err.value.code == "invalid_domain"


def test_a_bare_host_is_lower_cased_and_a_store_is_refused():
    assert ba.clean_domains(["Almarai.COM", "almarai.com", " www.meliha.ae "]) == ["almarai.com", "www.meliha.ae"]
    with pytest.raises(ba.BrandRequestError) as err:
        ba.clean_domains(["noon.com"])
    assert err.value.code == "blocked_domain"


def test_the_brand_and_synonyms_are_checked():
    for bad in ("", "   ", "GENERIC / NO BRAND", "-", "x" * 101, "a\x00b"):
        with pytest.raises(ba.BrandRequestError) as err:
            ba.clean_brand(bad)
        assert err.value.field == "brand"
    assert ba.clean_brand("  Saba   Sanabel ") == "Saba Sanabel"
    assert ba.clean_synonyms("سبع سنابل، Sabaa Sanabel, saba sanabel,SABAA SANABEL", "Saba Sanabel") == \
        ["سبع سنابل", "Sabaa Sanabel"]                       # the brand and repeats dropped, Arabic comma split
    assert len(ba.clean_synonyms([f"name {i}" for i in range(10)])) == 10
    with pytest.raises(ba.BrandRequestError) as err:
        ba.clean_synonyms([f"name {i}" for i in range(11)])
    assert err.value.code == "too_many_synonyms"
    with pytest.raises(ba.BrandRequestError) as err:
        ba.clean_synonyms(["a, b"])
    assert err.value.code == "invalid_synonym"


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------

MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["Almarai", "Al Marai", "المراعي"]}}


def _row(n, brand, name="PRODUCT X 1KG", brand_ar="", discovered=(), name_ar=""):
    return {"row_number": n, "name": name, "brand": brand, "brand_ar": brand_ar, "name_ar": name_ar,
            "discovered": list(discovered)}


def test_suggestions_list_only_unmapped_brands_with_counts_arabic_name_and_store_spellings():
    rows = [
        _row(1, "Meliha", "MELIHA FRESH MILK 1L", "مليحة", ["Mleiha"]), _row(2, "meliha ", "MELIHA LABAN 1L", "مليحة", ["Mleiha"]),
        _row(3, "MELIHA", "MELIHA YOGHURT", "", ["Mleiha", "Maliha"]),
        _row(4, "SABA SANABEL", "SABA SANABEL FLOUR 1KG", "سبع سنابل"),
        _row(5, "ALMARAI", "ALMARAI MILK 1L"),                       # mapped
        _row(6, "AL MARAI", "ALMARAI MILK 2L"),                      # a synonym of a mapped brand
        _row(7, "GENERIC / NO BRAND", "CANDY"), _row(8, "-", "GUM"), _row(9, "", "NAMELESS"),   # no brand
        _row(10, "", "SOMETHING", brand_ar="تارجت"),                  # only the Arabic cell: that is the brand
    ]
    aliases = [("Saba Sanabel", "Sabaa Sanabel", 3, 0), ("SABA  SANABEL", "Sabaa Sanabel", 1, 0),
               ("Other Brand", "Not Ours", 5, 0), ("saba sanabel", "سبع سنابل", 2, 0)]
    out = ba.suggestions(rows, MAPPINGS, aliases)
    assert [b["brand"] for b in out] == ["Meliha", "SABA SANABEL", "تارجت"]            # most rows first; most common spelling
    meliha, saba, target = out
    assert meliha == {"brand": "Meliha", "rows": 3, "brand_ar": "مليحة", "synonyms": ["Mleiha", "Maliha"],
                      "official_domain": ""}
    assert (saba["rows"], saba["brand_ar"], saba["synonyms"]) == (1, "سبع سنابل", ["Sabaa Sanabel"])   # learned; Arabic not repeated
    assert (target["rows"], target["brand_ar"], target["synonyms"]) == (1, "", [])    # the brand is already Arabic


def test_suggestions_cap_the_spellings_and_use_a_mapped_brand_a_name_starts_with():
    found = [f"Spelling {i}" for i in range(12)]
    out = ba.suggestions([_row(1, "NEWBRAND", "NEWBRAND X", discovered=found)], {}, [])
    assert out[0]["synonyms"] == found[:ba.SUGGESTED_SYNONYMS]
    # the brand cell is unknown but the product name starts with a mapped brand: the search reads it as that brand
    assert ba.suggestions([_row(1, "ALM", "ALMARAI MILK 1L")], MAPPINGS, []) == []


# ---------------------------------------------------------------------------
# the bridge, with the sheet and the paid search mocked
# ---------------------------------------------------------------------------

class FakeSheet:
    """A Brands Mapping worksheet that records what is written to it."""

    def __init__(self, rows=None):
        self.rows = [list(HEADERS)] + [list(r) for r in (rows or [])]
        self.reads = 0
        self.appended = []
        self.cells = []
        self.title = "Brands Mapping"

    def worksheet(self, title):
        assert title == "Brands Mapping"
        return self

    def get_all_values(self):
        self.reads += 1
        return [list(r) for r in self.rows]

    def append_rows(self, values, value_input_option=None, **kw):
        self.appended.append((values, value_input_option))
        self.rows.extend(values)

    def update_cell(self, row, col, value):
        self.cells.append((row, col, value))


@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import config
    import google_sheets
    import local_cache_db

    sheet = FakeSheet([["Almarai", "Al Marai, المراعي", "Sutas", "", "almarai.com"]])
    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "https://docs.example/sheet")
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "_open_spreadsheet", lambda client, name: sheet)
    monkeypatch.setattr(google_sheets, "_with_learning", lambda mappings: pytest.fail("the assistant reads the sheet only"))
    queued = []
    monkeypatch.setattr(local_cache_db, "add_pending_harvest_domains", lambda domains: queued.extend(domains) or queued)
    cli_bridge.sheet, cli_bridge.queued = sheet, queued
    return cli_bridge


def _call(bridge, action, params):
    return bridge.ACTIONS[action](params)


def test_brand_add_appends_one_row_with_the_right_columns(bridge, tmp_path):
    cache = tmp_path / "brand_mappings_cache.json"
    cache.write_text("{}", encoding="utf-8")
    out = _call(bridge, "brand_add", {"brand": " Saba   Sanabel ", "synonyms": ["سبع سنابل", "Sabaa Sanabel"],
                                      "official_domains": ["SabaSanabel.com"]})
    assert out == {"status": "success", "added": ["Saba Sanabel"], "skipped": [], "harvest_queued": ["sabasanabel.com"]}
    assert bridge.sheet.appended == [([["Saba Sanabel", "سبع سنابل, Sabaa Sanabel", "", "", "sabasanabel.com"]], "RAW")]
    assert bridge.sheet.reads == 1                                  # one fresh read of the sheet
    assert bridge.queued == ["sabasanabel.com"]                     # indexed at the next worker run
    assert not cache.exists()                                       # the next run reads the sheet again


def test_brand_add_without_a_site_queues_nothing_and_a_synonym_of_another_brand_is_not_repeated(bridge):
    out = _call(bridge, "brand_add", {"brand": "Meliha", "synonyms": ["Mleiha", "Al Marai"], "official_domains": []})
    assert out["status"] == "success" and out["harvest_queued"] == []
    assert bridge.sheet.appended[0][0] == [["Meliha", "Mleiha", "", "", ""]]
    assert bridge.queued == []


@pytest.mark.parametrize("brand", ["Almarai", "  ALMARAI ", "al-marai", "AL MARAI", "المراعي"])
def test_brand_add_refuses_a_brand_that_is_already_mapped(bridge, brand):
    out = _call(bridge, "brand_add", {"brand": brand, "synonyms": ["x"], "official_domains": ["almarai.ae"]})
    assert (out["status"], out["code"]) == ("duplicate", "duplicate")
    assert bridge.sheet.appended == [] and bridge.queued == []
    assert bridge.sheet.reads == 1                                   # it looked at the sheet as it is now


@pytest.mark.parametrize("params, code, field", [
    ({"brand": ""}, "invalid_brand", "brand"),
    ({"brand": "GENERIC"}, "invalid_brand", "brand"),
    ({"brand": "Meliha", "official_domains": ["https://meliha.ae"]}, "invalid_domain", "official_domains"),
    ({"brand": "Meliha", "official_domains": ["meliha.ae/shop"]}, "invalid_domain", "official_domains"),
    ({"brand": "Meliha", "official_domains": ["noon.com"]}, "blocked_domain", "official_domains"),
    ({"brand": "Meliha", "synonyms": [f"s{i}" for i in range(11)]}, "too_many_synonyms", "synonyms"),
    ({"items": []}, "too_many_brands", "items"),
    ({"items": [{"brand": "Fine"}, {"brand": "Bad", "official_domains": ["bad domain"]}]}, "invalid_domain", "official_domains"),
])
def test_brand_add_refuses_bad_input_before_touching_the_sheet(bridge, monkeypatch, params, code, field):
    import google_sheets
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: pytest.fail("the sheet must not be opened"))
    out = _call(bridge, "brand_add", params)
    assert (out["status"], out["code"], out["field"]) == ("invalid", code, field)
    assert bridge.sheet.appended == [] and bridge.queued == []


def test_brand_add_all_writes_every_new_brand_in_one_request_and_skips_the_mapped_ones(bridge):
    out = _call(bridge, "brand_add", {"items": [
        {"brand": "Meliha", "synonyms": ["Mleiha"]}, {"brand": "almarai"}, {"brand": "Saba Sanabel", "synonyms": []},
        {"brand": "MELIHA"}]})
    assert out["status"] == "success" and out["added"] == ["Meliha", "Saba Sanabel"]
    assert out["skipped"] == [{"brand": "almarai", "reason": "duplicate"}, {"brand": "MELIHA", "reason": "duplicate"}]
    assert len(bridge.sheet.appended) == 1                                           # one write for the whole list
    assert bridge.sheet.appended[0][0] == [["Meliha", "Mleiha", "", "", ""], ["Saba Sanabel", "", "", "", ""]]


def test_brand_add_follows_the_sheets_own_column_order_and_adds_a_missing_domains_column(bridge):
    bridge.sheet.rows = [["Synonyms", "Brand"], ["x", "Other"]]
    out = _call(bridge, "brand_add", {"brand": "Meliha", "synonyms": ["Mleiha"], "official_domains": ["meliha.ae"]})
    assert out["status"] == "success"
    assert bridge.sheet.cells == [(1, 3, "Official domains")]
    assert bridge.sheet.appended[0][0] == [["Mleiha", "Meliha", "meliha.ae", "", ""]]       # padded to five columns


def test_brand_add_reports_a_sheet_it_could_not_write_without_details(bridge, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("secret detail: token abc")
    bridge.sheet.append_rows = broken
    out = _call(bridge, "brand_add", {"brand": "Meliha"})
    assert out["status"] == "failed" and "secret detail" not in json.dumps(out)
    assert bridge.queued == []


def test_brand_suggestions_reads_the_queue_and_the_sheet_and_writes_nothing(bridge, monkeypatch):
    import local_cache_db
    monkeypatch.setattr(local_cache_db, "queue_brand_rows", lambda: [
        _row(1, "MELIHA", discovered=["Mleiha"], brand_ar="مليحة"), _row(2, "MELIHA"), _row(3, "ALMARAI"), _row(4, "GENERIC")])
    monkeypatch.setattr(local_cache_db, "get_learned_brand_aliases", lambda: [("MELIHA", "Maliha", 2, 0)])
    out = _call(bridge, "brand_suggestions", {})
    assert out == {"status": "success", "rows": 4, "brands": [
        {"brand": "MELIHA", "rows": 2, "brand_ar": "مليحة", "synonyms": ["Mleiha", "Maliha"], "official_domain": ""}]}
    assert bridge.sheet.appended == [] and bridge.sheet.cells == [] and bridge.queued == []


def test_brand_suggestions_does_not_list_every_brand_when_the_sheet_cannot_be_read(bridge, monkeypatch):
    import google_sheets
    import local_cache_db
    monkeypatch.setattr(local_cache_db, "queue_brand_rows", lambda: [_row(1, "MELIHA")])
    monkeypatch.setattr(google_sheets, "_open_spreadsheet", lambda *a: (_ for _ in ()).throw(RuntimeError("no access")))
    out = _call(bridge, "brand_suggestions", {})
    assert out["status"] == "failed" and "no access" not in json.dumps(out)


class SearchSpy:
    """Stands in for SerperWebProvider._request: counts the queries and returns a fixed body."""

    def __init__(self, organic=None, fail=False):
        self.calls, self.organic, self.fail, self.hedge = [], organic, fail, None

    def __call__(self, provider, query, hl):
        self.calls.append(query)
        self.hedge = provider.hedge
        if self.fail:
            raise RuntimeError("HTTP 500 secret detail")
        return {"organic": [{"link": r["link"], "title": r["title"], "position": i + 1}
                            for i, r in enumerate(self.organic or [])]}


@pytest.fixture
def paid(monkeypatch):
    from catalog_match import settings
    from catalog_match.providers.serper_web import SerperWebProvider
    import local_cache_db

    spent = []
    monkeypatch.setattr(settings, "serper_api_key", lambda: "test-key")
    monkeypatch.setattr(local_cache_db, "record_search_spend", lambda outcome, run_id=None: spent.append((outcome, run_id)))

    def install(spy):
        monkeypatch.setattr(SerperWebProvider, "_request", lambda provider, query, hl: spy(provider, query, hl))
        return spy
    install.spent = spent
    return install


def test_the_official_site_costs_exactly_one_query_which_is_recorded_in_the_ledger(bridge, paid):
    import local_cache_db
    spy = paid(SearchSpy(RESULTS))
    out = _call(bridge, "brand_official_site", {"brand": "Almarai"})
    assert spy.calls == ['"Almarai" official website'] and spy.hedge is False       # one request, never a hedged copy
    assert out["status"] == "success" and out["queries"] == 1
    assert [c["domain"] for c in out["candidates"]] == ["almarai.com", "news.example.org"]
    (outcome, run_id), = paid.spent
    assert run_id == "brand-site"
    items = local_cache_db.spend_from_outcome(outcome)
    assert [(p, calls) for p, calls, _usd in items] == [("serper_web", 1)] and items[0][2] > 0
    assert bridge.sheet.appended == [] and bridge.queued == []                        # it never writes anything


def test_the_official_site_with_no_answer_costs_nothing_and_without_a_key_sends_nothing(bridge, paid, monkeypatch):
    from catalog_match import settings
    spy = paid(SearchSpy(fail=True))
    out = _call(bridge, "brand_official_site", {"brand": "Almarai"})
    assert out["status"] == "failed" and "secret detail" not in json.dumps(out) and paid.spent == []   # not answered: not billed
    monkeypatch.setattr(settings, "serper_api_key", lambda: "")
    spy.calls.clear()
    assert _call(bridge, "brand_official_site", {"brand": "Almarai"})["code"] == "no_key" and spy.calls == []
    monkeypatch.setattr(settings, "serper_api_key", lambda: "test-key")
    assert _call(bridge, "brand_official_site", {"brand": "GENERIC"})["status"] == "invalid" and spy.calls == []


def test_an_empty_answer_is_still_one_billed_query(bridge, paid):
    paid(SearchSpy([]))
    out = _call(bridge, "brand_official_site", {"brand": "Nonexistent Brand"})
    assert out["candidates"] == [] and paid.spent[0][0]["provider_health"][0]["status"] == "empty"


def test_the_actions_are_registered_and_the_assistant_has_no_secret_literals():
    import cli_bridge
    assert {"brand_suggestions", "brand_official_site", "brand_add"} <= set(cli_bridge.ACTIONS)
    source = open(ba.__file__, encoding="utf-8").read() + open(__file__, encoding="utf-8").read()
    assert not re.search(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}", source)


# ---------------------------------------------------------------------------
# indexing the site at the next worker run
# ---------------------------------------------------------------------------

class FakeReport:
    def __init__(self, status, urls):
        self.status, self.product_urls = status, urls

    def as_dict(self):
        return {"status": self.status, "product_urls": self.product_urls}


class FakeHarvester:
    def __init__(self, outcomes):
        self.outcomes, self.stores = dict(outcomes), []

    def harvest(self, store, on_urls=None, max_urls=None, max_sitemaps=None, discover=False):
        self.stores.append(store)
        on_urls([("https://" + store.hosts[0] + "/products/x", None)])
        return FakeReport(*self.outcomes[store.name])


class FakeIndex:
    def __init__(self):
        self.rows, self.harvests = [], []

    def begin_harvest(self, store):
        return 1

    def upsert(self, store, batch):
        self.rows.extend((store, u) for u, _l in batch)

    def finish_harvest(self, store, started, report):
        self.harvests.append((store, report))


def test_a_queued_site_is_harvested_as_a_store_and_leaves_the_queue_once_read():
    queue = ["a.com", "b.com", "c.com", "d.com", "e.com"]
    removed = []
    harvester = FakeHarvester({"a.com": ("ok", 12), "b.com": ("error", 0), "c.com": ("blocked", 0), "d.com": ("empty", 0)})
    index = FakeIndex()
    out = ba.harvest_pending(harvester=harvester, db=index, pending=lambda: queue, finish=removed.extend)
    assert [(r["domain"], r["status"]) for r in out] == [("a.com", "ok"), ("b.com", "error"), ("c.com", "blocked")]   # three at most
    assert removed == ["a.com", "c.com"]                                  # an unanswered site is tried again next run
    store = harvester.stores[0]
    assert (store.key, store.hosts, store.base_url) == ("b:a.com", ("a.com", "www.a.com"), "https://a.com")
    assert store.is_product("https://a.com/products/some-milk-1l") and not store.is_product("https://a.com/about")
    assert index.rows[0] == ("b:a.com", "https://a.com/products/x") and index.harvests[0][0] == "b:a.com"


def test_the_harvest_stops_at_its_time_budget_never_raises_and_keeps_store_keys_short():
    ticks = iter(range(0, 1000, 100))
    harvester = FakeHarvester({"a.com": ("ok", 1), "b.com": ("ok", 1)})
    out = ba.harvest_pending(budget_s=150, harvester=harvester, db=FakeIndex(), clock=lambda: next(ticks),
                             pending=lambda: ["a.com", "b.com"], finish=lambda d: None)
    assert [r["domain"] for r in out] == ["a.com"]
    assert ba.harvest_pending(pending=lambda: (_ for _ in ()).throw(RuntimeError("db down"))) == []
    long_key = ba.store_key("a-very-long-brand-name-website.example.com")
    assert len(long_key) <= 32 and long_key != ba.store_key("a-very-long-brand-name-website.example.org")
    assert ba.store_key("a.com") == "b:a.com"


def test_the_worker_starts_the_harvest_only_behind_local_index_enabled(monkeypatch):
    import main
    from catalog_match import settings
    ran = []
    monkeypatch.setattr(ba, "harvest_pending", lambda *a, **k: ran.append(1) or [])
    monkeypatch.setattr(settings, "local_index_enabled", lambda: False)
    main._harvest_pending_brand_sites()
    assert ran == []
    monkeypatch.setattr(settings, "local_index_enabled", lambda: True)
    main._harvest_pending_brand_sites()
    assert ran == [1]
    monkeypatch.setattr(ba, "harvest_pending", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    main._harvest_pending_brand_sites()                                  # never stops the run
    source = open(main.__file__, encoding="utf-8").read()
    assert "_forget_slow_hosts()\n        _harvest_pending_brand_sites()\n" in source        # at the start of the worker run


# ---------------------------------------------------------------------------
# the real database: what the queue holds, and the pending sites
# ---------------------------------------------------------------------------

ROWS = (931101, 931102, 931103)


def test_the_queue_rows_carry_the_arabic_brand_and_the_discovered_spellings(mariadb_or_skip):
    db = mariadb_or_skip
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM automation_queue WHERE `row_number` IN (%s, %s, %s)", ROWS)
        conn.commit()
        db.add_many_to_queue([
            db.queue_input(ROWS[0], "", "BRANDTEST MILK 1L", "BRANDTEST", "q", payload={"brand_ar": "براندتست", "name_ar": "حليب"}),
            db.queue_input(ROWS[1], "", "BRANDTEST LABAN", "BRANDTEST", "q", payload=None),
            db.queue_input(ROWS[2], "", "BRANDTEST YOGHURT", "BRANDTEST", "q", payload={"brand_ar": "x"}),
        ])
        with conn.cursor() as cur:
            cur.execute("UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s",
                        (json.dumps({"outcome": {"discovered_brands": ["Brand Test", ""]}}), ROWS[0]))
            cur.execute("UPDATE automation_queue SET trace_json = 'not json', payload_json = '{broken' WHERE `row_number` = %s",
                        (ROWS[2],))
        conn.commit()
        rows = {r["row_number"]: r for r in db.queue_brand_rows() if r["row_number"] in ROWS}
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM automation_queue WHERE `row_number` IN (%s, %s, %s)", ROWS)
        conn.commit()
        conn.close()
    assert rows[ROWS[0]] == {"row_number": ROWS[0], "name": "BRANDTEST MILK 1L", "brand": "BRANDTEST",
                             "brand_ar": "براندتست", "name_ar": "حليب", "discovered": ["Brand Test"]}
    assert (rows[ROWS[1]]["brand_ar"], rows[ROWS[1]]["discovered"]) == ("", [])
    assert (rows[ROWS[2]]["brand"], rows[ROWS[2]]["brand_ar"], rows[ROWS[2]]["discovered"]) == ("BRANDTEST", "", [])


def test_pending_sites_are_kept_without_repeats_and_removed_when_indexed(mariadb_or_skip):
    db = mariadb_or_skip
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM system_settings WHERE `key` = %s", (db.PENDING_HARVEST_KEY,))
        conn.commit()
        assert db.pending_harvest_domains() == []
        assert db.add_pending_harvest_domains(["A.com", "b.com", "a.com"]) == ["a.com", "b.com"]
        assert db.add_pending_harvest_domains(["c.com"]) == ["a.com", "b.com", "c.com"]
        assert db.remove_pending_harvest_domains(["A.COM", "zzz.com"]) == ["b.com", "c.com"]
        assert db.pending_harvest_domains() == ["b.com", "c.com"]
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM system_settings WHERE `key` = %s", (db.PENDING_HARVEST_KEY,))
        conn.commit()
        conn.close()
