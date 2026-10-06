"""«عبّي جدول الماركات»: one Brands Mapping suggestion for every missing brand, from evidence only
(catalog_match.brand_assistant.bulk_suggestions / bulk_items and the cli_bridge brand_bulk_* / brand_undo actions).

- every brand the rows name that the sheet's mapping does not know gets one proposal with a confidence and its evidence
  in plain Levantine: approved reviews and the spellings they taught, the local index's product pages named after the
  brand and their stores, the cached official site (only a host that carries the brand), the store spellings seen;
- 'high' (two kinds, or two approved images) is pre-ticked, 'low' (one kind) is not, 'none' cannot be ticked and says
  «ما لقينا دليل كافي»;
- «اعتمد المحدد» writes each ticked brand's OWN proposal (never what the page sends, never a brand without evidence) in
  batches of one fresh read and one append, is idempotent, logs what it wrote and can undo one brand whose row is still
  exactly that; «دوّر عالمواقع الرسمية» sends at most BULK_SITE_LOOKUPS queries (one per brand, cached, never again).
"""

import json

import pytest

from catalog_match import brand_assistant as ba

HEADERS = ["Brand", "Synonyms", "Excluded Competitors", "Sub-brands", "Official domains"]
MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["Almarai", "Al Marai"]}}


def _row(n, brand, name="", brand_ar="", discovered=()):
    return {"row_number": n, "name": name or f"{brand} product {n}", "brand": brand, "brand_ar": brand_ar,
            "name_ar": "", "discovered": list(discovered)}


class Hit:
    def __init__(self, url, slug_text="", page_title="", store="s"):
        self.url, self.slug_text, self.page_title, self.store = url, slug_text, page_title, store


def _index(table):
    """index_lookup over {brand: {products, stores}}."""
    return lambda brand: table.get(brand, {"products": 0, "stores": []})


# ---------------------------------------------------------------------------
# the proposals
# ---------------------------------------------------------------------------

def test_each_missing_brand_gets_one_proposal_with_its_confidence_and_evidence():
    rows = ([_row(i, "MELIHA", brand_ar="مليحة") for i in range(1, 4)]
            + [_row(4, "RIO MARIE", discovered=["Rio Mare"]), _row(5, "RIO MARIE", discovered=["Rio Mare"])]
            + [_row(6, "SUP/T", discovered=["Super Tasty"])]
            + [_row(7, "ZZQX")]
            + [_row(8, "Almarai"), _row(9, "N/A"), _row(10, "")])
    approvals = [("MELIHA", "lulu.ae", 2, 0), ("Meliha", "carrefouruae.com", 1, 0), ("ZZQX", "x.com", 0, 0)]
    aliases = [("RIO MARIE", "Rio Mare", 3, 0)]
    index = _index({"RIO MARIE": {"products": 4, "stores": ["carrefouruae.com", "lulu.ae"]},
                    "ZZQX": {"products": 1, "stores": ["noon.com"]}})
    out = ba.bulk_suggestions(rows, MAPPINGS, aliases, approvals, index, {})
    by = {b["brand"]: b for b in out}
    assert set(by) == {"MELIHA", "RIO MARIE", "SUP/T", "ZZQX"}            # mapped, placeholder and empty left out

    meliha = by["MELIHA"]
    assert (meliha["confidence"], meliha["checked"], meliha["selectable"]) == ("high", True, True)
    assert meliha["evidence"] == [{"kind": "reviews", "text": "اعتمدت 3 صور لهالماركة بالمراجعة (من lulu.ae، carrefouruae.com)."}]
    assert meliha["brand_ar"] == "مليحة" and meliha["rows"] == 3

    rio = by["RIO MARIE"]
    assert rio["confidence"] == "high" and rio["synonyms"] == ["Rio Mare"]
    kinds = [e["kind"] for e in rio["evidence"]]
    assert kinds == ["aliases", "index", "spellings"]
    assert rio["evidence"][1]["text"] == "لقينا 4 منتجات باسمها بفهرس المتاجر (carrefouruae.com، lulu.ae)."

    sup = by["SUP/T"]
    assert (sup["confidence"], sup["checked"], sup["selectable"]) == ("low", False, True)
    assert sup["evidence"] == [{"kind": "spellings", "text": "البحث لقاها بالمتاجر مكتوبة: Super Tasty."}]

    # one index page and no approval is no evidence: shown, never selectable
    zz = by["ZZQX"]
    assert (zz["confidence"], zz["checked"], zz["selectable"]) == ("none", False, False)
    assert zz["evidence"] == [{"kind": "none", "text": ba.NO_EVIDENCE_TEXT}] and ba.NO_EVIDENCE_TEXT == "ما لقينا دليل كافي"
    assert [b["confidence"] for b in out] == ["high", "high", "low", "none"]       # high first, then most rows
    assert out[0]["brand"] == "MELIHA"


def test_one_approved_image_alone_is_low_and_two_are_high():
    rows = [_row(1, "NEWCO")]
    one = ba.bulk_suggestions(rows, {}, [], [("NEWCO", "lulu.ae", 1, 0)], None, {})[0]
    assert one["confidence"] == "low" and one["evidence"][0]["text"].startswith("اعتمدت صورة وحدة")
    two = ba.bulk_suggestions(rows, {}, [], [("NEWCO", "lulu.ae", 2, 0)], None, {})[0]
    assert two["confidence"] == "high" and two["evidence"][0]["text"].startswith("اعتمدت صورتين")


def test_only_a_cached_site_that_carries_the_brand_is_used():
    rows = [_row(1, "Saba Sanabel"), _row(2, "Mai Dubai"), _row(3, "Pure Harvest")]
    sites = {ba.brand_key("Saba Sanabel"): {"domain": "sabasanabel.com"},
             ba.brand_key("Mai Dubai"): {"domain": "carrefouruae.com"},          # a store is never a brand's site
             ba.brand_key("Pure Harvest"): {"domain": "unrelated-site.com"}}
    by = {b["brand"]: b for b in ba.bulk_suggestions(rows, {}, [], [], None, sites)}
    assert by["Saba Sanabel"]["official_domain"] == "sabasanabel.com" and by["Saba Sanabel"]["confidence"] == "low"
    assert {"kind": "site", "text": "موقعها الرسمي: sabasanabel.com."} in by["Saba Sanabel"]["evidence"]
    assert by["Mai Dubai"]["official_domain"] == "" and by["Mai Dubai"]["confidence"] == "none"
    assert by["Pure Harvest"]["official_domain"] == "" and by["Pure Harvest"]["site_searched"] is True
    assert by["Saba Sanabel"]["site_searched"] is True


def test_an_index_that_fails_says_nothing():
    def broken(brand):
        raise RuntimeError("db down")
    out = ba.bulk_suggestions([_row(1, "NEWCO", discovered=["Nu Co"])], {}, [], [], broken, {})
    assert out[0]["confidence"] == "low" and [e["kind"] for e in out[0]["evidence"]] == ["spellings"]


def test_the_index_evidence_needs_the_whole_brand_in_the_slug_or_the_title():
    asked = []

    def find(required, extra, limit):
        asked.append((list(required), list(extra), limit))
        return [Hit("https://www.carrefouruae.com/mafuae/en/al-ain-water-500ml/p/1", "al ain water 500ml"),
                Hit("https://www.luluhypermarket.com/en-ae/x/p/2", "", "Al Ain Water 1.5L"),
                Hit("https://www.carrefouruae.com/mafuae/en/ain-drops/p/3", "ain drops"),         # not the whole brand
                Hit("https://www.carrefouruae.com/mafuae/en/al-ain-juice/p/4", "al ain juice")]
    out = ba.index_evidence("Al Ain", find)
    assert out == {"products": 3, "stores": ["carrefouruae.com", "luluhypermarket.com"]}
    assert asked == [(["ain"], [], ba.INDEX_ROWS)]
    assert ba.index_evidence("--", find) == {"products": 0, "stores": []}


def test_host_carries_brand_and_the_site_kept_for_the_cache():
    assert ba.host_carries_brand("www.almarai.com", "Almarai") and ba.host_carries_brand("sabasanabel.com", "Saba Sanabel")
    assert not ba.host_carries_brand("news.example.org", "Almarai")
    cands = [{"domain": "news.example.org"}, {"domain": "almarai.com"}]
    assert ba.site_for_cache(cands, "Almarai") == "almarai.com"
    assert ba.site_for_cache([{"domain": "news.example.org"}], "Almarai") == ""
    assert ba.site_for_cache([{"domain": "carrefouruae.com"}], "Carrefour") == ""


def test_bulk_items_write_the_proposals_own_values_and_never_a_brand_without_evidence():
    rows = [_row(1, "MELIHA", brand_ar="مليحة", discovered=["Mleiha"]), _row(2, "ZZQX"), _row(3, "SUP/T", discovered=["Super Tasty"])]
    sites = {ba.brand_key("MELIHA"): {"domain": "meliha.ae"}}
    proposals = ba.bulk_suggestions(rows, {}, [], [("MELIHA", "lulu.ae", 2, 0)], None, sites)
    items, skipped = ba.bulk_items(proposals, ["  meliha ", "ZZQX", "Nope", "MELIHA", "SUP/T", 7, ""])
    assert items == [{"brand": "MELIHA", "synonyms": ["مليحة", "Mleiha"], "official_domains": ["meliha.ae"]},
                     {"brand": "SUP/T", "synonyms": ["Super Tasty"], "official_domains": []}]
    assert skipped == [{"brand": "ZZQX", "reason": "no_evidence"}, {"brand": "Nope", "reason": "gone"},
                       {"brand": "MELIHA", "reason": "repeated"}]


# ---------------------------------------------------------------------------
# the bridge, with the sheet, the database and the paid search mocked
# ---------------------------------------------------------------------------

class FakeSheet:
    def __init__(self, rows=None):
        self.rows = [list(HEADERS)] + [list(r) for r in (rows or [])]
        self.reads = 0
        self.appended = []
        self.deleted = []
        self.title = "Brands Mapping"

    def worksheet(self, title):
        assert title == "Brands Mapping"
        return self

    def get_all_values(self):
        self.reads += 1
        return [list(r) for r in self.rows]

    def append_rows(self, values, value_input_option=None, **kw):
        self.appended.append([list(v) for v in values])
        self.rows.extend(list(v) for v in values)

    def delete_rows(self, index):
        self.deleted.append(index)
        del self.rows[index - 1]


class FakeIndex:
    table = {}

    def count(self, fresh=False):
        return 5

    def find(self, required, extra, limit):
        return FakeIndex.table.get(tuple(required), [])


@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import config
    import google_sheets
    import local_cache_db
    from catalog_match import local_index

    sheet = FakeSheet([["Almarai", "Al Marai, المراعي", "Sutas", "", "almarai.com"]])
    state = {"queue": [], "aliases": [], "approvals": [], "sites": {}, "writes": [], "queued": [], "unqueued": []}
    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "https://docs.example/sheet")
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "_open_spreadsheet", lambda client, name: sheet)
    monkeypatch.setattr(google_sheets, "cached_products", lambda: None)
    monkeypatch.setattr(local_cache_db, "queue_brand_rows", lambda: list(state["queue"]))
    monkeypatch.setattr(local_cache_db, "get_learned_brand_aliases", lambda: list(state["aliases"]))
    monkeypatch.setattr(local_cache_db, "get_brand_source_counts", lambda: list(state["approvals"]))
    monkeypatch.setattr(local_cache_db, "brand_sites", lambda: dict(state["sites"]))
    monkeypatch.setattr(local_cache_db, "save_brand_site",
                        lambda key, brand, domain: state["sites"].__setitem__(key, {"brand": brand, "domain": domain}))

    def log(entries):
        for e in entries:
            state["writes"].append(dict(e, id=len(state["writes"]) + 1, undone=False))
        return len(entries)

    def last(key):
        live = [w for w in state["writes"] if w["brand_key"] == key and not w["undone"]]
        return dict(live[-1]) if live else None

    def undone(write_id):
        state["writes"][write_id - 1]["undone"] = True
        return True

    monkeypatch.setattr(local_cache_db, "log_brand_writes", log)
    monkeypatch.setattr(local_cache_db, "last_brand_write", last)
    monkeypatch.setattr(local_cache_db, "mark_brand_write_undone", undone)
    monkeypatch.setattr(local_cache_db, "add_pending_harvest_domains", lambda d: state["queued"].extend(d) or d)
    monkeypatch.setattr(local_cache_db, "remove_pending_harvest_domains", lambda d: state["unqueued"].extend(d) or [])
    monkeypatch.setattr(local_index, "DbCatalogStore", FakeIndex)
    FakeIndex.table = {}
    cli_bridge.sheet, cli_bridge.state = sheet, state
    return cli_bridge


def _call(bridge, action, params):
    return bridge.ACTIONS[action](params)


def _seed(bridge):
    st = bridge.state
    st["queue"] = ([_row(i, "MELIHA", brand_ar="مليحة") for i in range(1, 4)]
                   + [_row(4, "SUP/T", discovered=["Super Tasty"]), _row(5, "ZZQX"), _row(6, "Almarai")])
    st["approvals"] = [("MELIHA", "lulu.ae", 2, 0)]
    st["sites"] = {ba.brand_key("MELIHA"): {"brand": "MELIHA", "domain": "meliha.ae"}}


def test_the_bulk_list_reads_only(bridge, monkeypatch):
    from catalog_match.providers.serper_web import SerperWebProvider
    monkeypatch.setattr(SerperWebProvider, "_request", lambda *a: pytest.fail("no paid search when listing"))
    _seed(bridge)
    FakeIndex.table = {("zzqx",): [Hit("https://www.noon.com/zzqx-thing/p/1", "zzqx thing")]}
    out = _call(bridge, "brand_bulk_suggestions", {})
    assert out["status"] == "success" and out["index"] is True
    assert [(b["brand"], b["confidence"]) for b in out["brands"]] == [("MELIHA", "high"), ("SUP/T", "low"), ("ZZQX", "none")]
    assert out["counts"] == {"high": 1, "low": 1, "none": 1}
    assert (out["sites_left"], out["site_cap"]) == (2, ba.BULK_SITE_LOOKUPS)
    assert bridge.sheet.appended == [] and bridge.state["writes"] == []
    json.dumps(out, ensure_ascii=False)


def test_the_bulk_list_is_an_error_when_the_sheet_cannot_be_read(bridge, monkeypatch):
    import google_sheets
    _seed(bridge)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None)
    out = _call(bridge, "brand_bulk_suggestions", {})
    assert out["status"] == "failed" and "brands" not in out


def test_approve_selected_writes_each_proposal_once_and_never_a_brand_without_evidence(bridge, monkeypatch):
    _seed(bridge)
    out = _call(bridge, "brand_bulk_add", {"brands": ["MELIHA", "SUP/T", "ZZQX", "Almarai", "Invented Brand"]})
    assert out["status"] == "success" and out["added"] == ["MELIHA", "SUP/T"]
    assert {"brand": "ZZQX", "reason": "no_evidence"} in out["skipped"]
    assert {"brand": "Almarai", "reason": "gone"} in out["skipped"] and {"brand": "Invented Brand", "reason": "gone"} in out["skipped"]
    assert bridge.sheet.appended == [[["MELIHA", "مليحة", "", "", "meliha.ae"], ["SUP/T", "Super Tasty", "", "", ""]]]
    assert out["harvest_queued"] == ["meliha.ae"]
    assert [(w["brand"], w["synonyms"], w["official_domains"]) for w in bridge.state["writes"]] == [
        ("MELIHA", "مليحة", "meliha.ae"), ("SUP/T", "Super Tasty", "")]
    # idempotent: the same click again writes nothing new
    again = _call(bridge, "brand_bulk_add", {"brands": ["MELIHA", "SUP/T"]})
    assert again["added"] == [] and len(bridge.sheet.appended) == 1
    assert {s["reason"] for s in again["skipped"]} == {"gone"}


def test_approve_selected_writes_in_batches_of_one_read_and_one_append(bridge, monkeypatch):
    monkeypatch.setattr(ba, "MAX_BATCH", 2)
    bridge.state["queue"] = [_row(i, f"BRAND{i}", discovered=[f"Brand Co {i}"]) for i in range(1, 6)]
    reads_before = bridge.sheet.reads
    out = _call(bridge, "brand_bulk_add", {"brands": [f"BRAND{i}" for i in range(1, 6)]})
    assert len(out["added"]) == 5 and [len(a) for a in bridge.sheet.appended] == [2, 2, 1]
    assert bridge.sheet.reads - reads_before == 1 + 3          # the proposals' read, then one fresh read per batch


@pytest.mark.parametrize("params", [{}, {"brands": "MELIHA"}, {"brands": []}, {"brands": ["x"] * 501}])
def test_approve_selected_refuses_a_bad_request_before_reading_anything(bridge, params):
    _seed(bridge)
    out = _call(bridge, "brand_bulk_add", params)
    assert out["status"] == "invalid" and out["code"] == "too_many_brands" and bridge.sheet.reads == 0


def test_a_sheet_that_refuses_the_write_says_so_and_logs_nothing(bridge, monkeypatch):
    import google_sheets
    _seed(bridge)

    def refuse(*a, **k):
        raise google_sheets.SheetTransientError("quota")
    monkeypatch.setattr(bridge.sheet, "append_rows", refuse)
    out = _call(bridge, "brand_bulk_add", {"brands": ["MELIHA"]})
    assert out["status"] == "failed" and out["added"] == [] and bridge.state["writes"] == []


def test_undo_removes_a_brand_the_assistant_wrote_only_while_its_row_is_unchanged(bridge):
    _seed(bridge)
    _call(bridge, "brand_bulk_add", {"brands": ["MELIHA", "SUP/T"]})
    rows_before = len(bridge.sheet.rows)
    out = _call(bridge, "brand_undo", {"brand": "MELIHA"})
    assert out == {"status": "success", "brand": "MELIHA"}
    assert bridge.sheet.deleted == [3] and len(bridge.sheet.rows) == rows_before - 1
    assert "MELIHA" not in [r[0] for r in bridge.sheet.rows] and bridge.state["unqueued"] == ["meliha.ae"]
    assert _call(bridge, "brand_undo", {"brand": "MELIHA"})["code"] == "not_ours"          # once only
    # the owner edited the row: nothing is removed
    bridge.sheet.rows[-1][1] = "Super Tasty, سوبر تيستي"
    out = _call(bridge, "brand_undo", {"brand": "SUP/T"})
    assert out["status"] == "invalid" and out["code"] == "changed" and bridge.sheet.deleted == [3]
    # a brand the owner added by hand is never the assistant's to remove
    assert _call(bridge, "brand_undo", {"brand": "Almarai"})["code"] == "not_ours"


def test_undo_of_a_row_already_gone_closes_the_log(bridge):
    _seed(bridge)
    _call(bridge, "brand_bulk_add", {"brands": ["MELIHA"]})
    del bridge.sheet.rows[-1]
    assert _call(bridge, "brand_undo", {"brand": "MELIHA"})["code"] == "missing"
    assert _call(bridge, "brand_undo", {"brand": "MELIHA"})["code"] == "not_ours"


class SearchSpy:
    def __init__(self, organic_by_brand=None, fail_after=None):
        self.calls, self.organic_by_brand, self.fail_after = [], organic_by_brand or {}, fail_after

    def __call__(self, provider, query, hl):
        assert provider.hedge is False
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("HTTP 500")
        self.calls.append(query)
        brand = query.split('"')[1]
        return {"organic": [{"link": link, "title": title, "position": i + 1}
                            for i, (link, title) in enumerate(self.organic_by_brand.get(brand, []))]}


@pytest.fixture
def paid(monkeypatch):
    from catalog_match import settings
    from catalog_match.providers.serper_web import SerperWebProvider
    import local_cache_db

    spent = []
    monkeypatch.setattr(settings, "serper_api_key", lambda: "configured")
    monkeypatch.setattr(local_cache_db, "record_search_spend", lambda outcome, run_id=None: spent.append(run_id))

    def install(spy):
        monkeypatch.setattr(SerperWebProvider, "_request", lambda provider, query, hl: spy(provider, query, hl))
        return spy
    install.spent = spent
    return install


def test_the_site_search_is_capped_one_query_per_brand_cached_and_never_repeated(bridge, paid):
    bridge.state["queue"] = [_row(i, f"Brandname{i:02d}") for i in range(1, 15)]
    bridge.state["sites"] = {ba.brand_key("Brandname01"): {"brand": "Brandname01", "domain": ""}}
    spy = paid(SearchSpy({"Brandname02": [("https://www.brandname02.com/", "Brandname02 official")]}))
    out = _call(bridge, "brand_bulk_sites", {"limit": 99})
    assert out == {"status": "success", "searched": ba.BULK_SITE_LOOKUPS, "found": 1, "left": 13 - ba.BULK_SITE_LOOKUPS,
                   "queries": ba.BULK_SITE_LOOKUPS}
    assert len(spy.calls) == ba.BULK_SITE_LOOKUPS and '"Brandname01" official website' not in spy.calls
    assert paid.spent == ["brand-site"] * ba.BULK_SITE_LOOKUPS
    assert bridge.state["sites"][ba.brand_key("Brandname02")]["domain"] == "brandname02.com"
    assert bridge.state["sites"][ba.brand_key("Brandname03")]["domain"] == ""
    spy.calls.clear()
    out = _call(bridge, "brand_bulk_sites", {})
    assert out["searched"] == 3 and out["left"] == 0 and len(spy.calls) == 3        # the cached ones are never searched again
    assert _call(bridge, "brand_bulk_sites", {})["searched"] == 0
    assert bridge.sheet.appended == []


def test_the_site_search_stops_at_the_first_failure_and_needs_a_key(bridge, paid, monkeypatch):
    from catalog_match import settings
    bridge.state["queue"] = [_row(i, f"Brandname{i:02d}") for i in range(1, 6)]
    spy = paid(SearchSpy(fail_after=2))
    out = _call(bridge, "brand_bulk_sites", {})
    assert (out["searched"], out["left"]) == (2, 3) and len(paid.spent) == 2          # the failed one is not billed
    monkeypatch.setattr(settings, "serper_api_key", lambda: "")
    spy.calls.clear()
    assert _call(bridge, "brand_bulk_sites", {})["code"] == "no_key" and spy.calls == []


def test_the_single_site_search_fills_the_cache(bridge, paid):
    paid(SearchSpy({"Almarai": [("https://www.almarai.com/en/", "Almarai | Official website")]}))
    out = _call(bridge, "brand_official_site", {"brand": "Almarai"})
    assert out["status"] == "success" and bridge.state["sites"][ba.brand_key("Almarai")]["domain"] == "almarai.com"


def test_the_actions_are_registered():
    import cli_bridge
    assert {"brand_bulk_suggestions", "brand_bulk_sites", "brand_bulk_add", "brand_undo"} <= set(cli_bridge.ACTIONS)


# ---------------------------------------------------------------------------
# the site cache and the write log (MariaDB)
# ---------------------------------------------------------------------------

def test_the_site_cache_and_the_write_log_in_the_database(mariadb_or_skip):
    db = mariadb_or_skip
    key = "-".join(("tst", "bulk", "brand"))
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM brand_sites WHERE brand_key = %s", (key,))
        cur.execute("DELETE FROM brand_writes WHERE brand_key = %s", (key,))
        conn.commit()
    finally:
        conn.close()
    db.save_brand_site(key, "Test Brand", "")
    db.save_brand_site(key, "Test Brand", "testbrand.com")
    assert db.brand_sites()[key]["domain"] == "testbrand.com"
    assert db.last_brand_write(key) is None
    assert db.log_brand_writes([{"brand_key": key, "brand": "Test Brand", "synonyms": "TB", "official_domains": ""}]) == 1
    entry = db.last_brand_write(key)
    assert (entry["brand"], entry["synonyms"]) == ("Test Brand", "TB")
    assert db.mark_brand_write_undone(entry["id"]) is True and db.mark_brand_write_undone(entry["id"]) is False
    assert db.last_brand_write(key) is None
