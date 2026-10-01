"""catalog_match.retrieve: pooled retrieval over every provider and query (D3, D7, D8).

Providers here are small stubs implementing the models.Provider protocol; the
query plan, the pool, the T1 early-stop (score.py) and default_providers() are real.
Sockets are blocked.
"""

import datetime as dt
import socket
import threading

import pytest

from catalog_match import settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, ProviderResult
from catalog_match.providers import default_providers
from catalog_match.query_plan import build_queries
from catalog_match.retrieve import Retriever, norm_image_url, retrieve, t1_early_stop
from catalog_match.score import has_tier1


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


MAPPINGS = {
    "almarai": {"brand": "Almarai", "synonyms": ["Almarai", "Al Marai", "المراعي"],
                "excluded_competitors": ["Al Rawabi", "Nadec"]},
}
VALID_EAN = "6281007035224"
CARREFOUR_PAGE = "https://www.carrefouruae.com/mafuae/en/fresh-milk/almarai-fresh-milk-full-fat-1l/p/12345"


def spec_for(**row):
    row.setdefault("name", "Almarai Fresh Milk Full Fat 1L")
    row.setdefault("brand", "Almarai")
    return build_sku_spec(row, MAPPINGS)


class Stub:
    """A search provider returning fixed candidates (or a status / exception) for every query."""

    kind = "search"

    def __init__(self, name, cands=(), status="ok", sanctioned=True, fallback=False, exc=None, per_query=None):
        self.name = name
        self.sanctioned = sanctioned
        self.fallback = fallback
        self.cands = list(cands)
        self.status = status
        self.exc = exc
        self.per_query = per_query if per_query is not None else {}
        self.calls = []
        self.lock = threading.Lock()

    def search(self, query, hl, spec):
        with self.lock:
            self.calls.append(query)
        if self.exc is not None:
            raise self.exc
        cands = self.per_query.get(query, self.cands)
        status = self.status if self.status != "ok" else ("ok" if cands else "empty")
        return ProviderResult(provider=self.name, status=status,
                              candidates=[c for c in cands] if status == "ok" else [])


def cand(url, **kw):
    return Candidate(image_url=url, **kw)


# ---------------------------------------------------------------------------
# Pooling, dedupe, exclusion, isolation
# ---------------------------------------------------------------------------

def test_pooling_and_dedupe():
    spec = spec_for()
    a = Stub("serper", [
        cand("https://cdn.example.com/p/milk-1l.jpg", page_url="https://blog.example.net/milk", title="milk"),
        cand("https://cdn.example.com/p/other.jpg", page_url="https://blog.example.net/other", title="other"),
    ])
    b = Stub("second", [
        cand("https://CDN.example.com/p/milk-1l.jpg?w=800", page_url=CARREFOUR_PAGE,
             page_title="Almarai Fresh Milk Full Fat 1L | Carrefour UAE", width=800, height=800),
        cand("https://img.example.org/rejected-by-reviewer.jpg", page_url="https://shop.example.org/x"),
    ])
    broken = Stub("broken", exc=RuntimeError("parser exploded"))

    res = retrieve(spec, [a, broken, b],
                   exclude_urls=["http://www.img.example.org/rejected-by-reviewer.jpg?w=100"])

    urls = [c.image_url for c in res.pool]
    assert len(res.pool) == 2                                   # milk deduped, reviewer negative dropped
    assert not any("rejected-by-reviewer" in u for u in urls)
    milk = next(c for c in res.pool if "milk-1l" in c.image_url)
    assert milk.consensus_count == 2                            # serper + second, not x queries
    assert milk.image_url == "https://cdn.example.com/p/milk-1l.jpg"   # full-size rendition kept
    # The more trusted page evidence (UAE retailer) travels with the merged candidate.
    assert milk.page_url == CARREFOUR_PAGE
    assert milk.page_title == "Almarai Fresh Milk Full Fat 1L | Carrefour UAE"
    other = next(c for c in res.pool if "other" in c.image_url)
    assert other.consensus_count == 1

    # The raising provider is isolated with health 'error'; the others' candidates remain.
    broken_health = [h for h in res.health if h.provider == "broken"]
    assert broken_health and all(h.status == "error" for h in broken_health)
    assert "parser exploded" in broken_health[0].error
    assert {h.status for h in res.health if h.provider in ("serper", "second")} == {"ok"}
    # Every planned query ran (no early stop was asked for).
    assert res.queries == [q.text for q in build_queries(spec)]
    assert a.calls == res.queries


def test_early_stop_after_q1_prevents_later_queries():
    spec = spec_for(name_ar="حليب المراعي كامل الدسم 1 لتر", barcode=VALID_EAN)
    plan = build_queries(spec)
    assert [q.query_id for q in plan] == ["Q1", "Q2", "Q3", "Q4"]
    serper = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])
    seen_pools = []

    def stop(pool):
        seen_pools.append(len(pool))
        return True

    res = retrieve(spec, [serper], early_stop=stop)
    assert serper.calls == [plan[0].text]
    assert res.queries == [plan[0].text]
    assert seen_pools == [1]                   # checked once, on the Q1 pool


def test_early_stop_is_checked_after_every_later_query():
    spec = spec_for(name_ar="حليب المراعي كامل الدسم 1 لتر", barcode=VALID_EAN)
    plan = build_queries(spec)
    serper = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")],
                  per_query={plan[1].text: [cand("https://cdn.example.com/p/found-by-q2.jpg")]})

    res = retrieve(spec, [serper], early_stop=lambda pool: any("found-by-q2" in c.image_url for c in pool))
    assert serper.calls == [plan[0].text, plan[1].text]      # Q3 and Q4 never sent
    assert res.queries == [plan[0].text, plan[1].text]


def test_t1_early_stop_uses_real_scoring():
    spec = spec_for()
    t1 = cand("https://cdn.mafrservices.com/sys-master-root/12345_main.jpg", page_url=CARREFOUR_PAGE,
              page_title="Almarai Fresh Milk Full Fat 1L | Carrefour UAE",
              title="Almarai Fresh Milk Full Fat 1L | Carrefour UAE")
    assert has_tier1(spec, [t1])
    serper = Stub("serper", [t1])
    res = retrieve(spec, [serper], early_stop=t1_early_stop(spec))
    assert len(serper.calls) == 1

    weak = cand("https://example-food-blog.com/milk.png", page_url="https://example-food-blog.com/post",
                title="Almarai milk")
    assert not has_tier1(spec, [weak])
    serper2 = Stub("serper", [weak])
    retrieve(spec, [serper2], early_stop=t1_early_stop(spec))
    assert len(serper2.calls) == len(build_queries(spec))


# ---------------------------------------------------------------------------
# GTIN lookup, fallback, budget, relaxations
# ---------------------------------------------------------------------------

class LookupStub:
    name = "off"
    kind = "lookup"
    sanctioned = True
    fallback = False

    def __init__(self, q1_started, lookup_started):
        self.q1_started = q1_started
        self.lookup_started = lookup_started
        self.saw_q1_running = None
        self.calls = 0

    def lookup(self, spec):
        self.calls += 1
        self.lookup_started.set()
        self.saw_q1_running = self.q1_started.wait(timeout=3)
        return ProviderResult(provider="off", status="ok", candidates=[
            cand("https://images.openfoodfacts.org/images/products/628/100/703/5224/front_en.12.400.jpg",
                 page_url=f"https://world.openfoodfacts.org/product/{VALID_EAN}",
                 page_title="Almarai Fresh Milk Full Fat 1 L", gtin_on_page=spec.gtin)])

    def search(self, query, hl, spec):  # pragma: no cover - retrieve must use lookup()
        raise AssertionError("a lookup provider is called through lookup(), once")


def test_off_lookup_runs_in_parallel_with_q1():
    spec = spec_for(barcode=VALID_EAN)
    q1_started, lookup_started = threading.Event(), threading.Event()
    off = LookupStub(q1_started, lookup_started)

    class Q1Probe(Stub):
        def search(self, query, hl, spec_):
            q1_started.set()
            self.saw_lookup_running = lookup_started.wait(timeout=3)
            return super().search(query, hl, spec_)

    serper = Q1Probe("serper", [cand("https://cdn.example.com/p/a.jpg")])
    res = retrieve(spec, [off, serper])
    assert off.calls == 1                               # once per SKU, not per query
    assert off.saw_q1_running is True and serper.saw_lookup_running is True
    off_cands = [c for c in res.pool if c.provider == "off"]
    assert len(off_cands) == 1 and off_cands[0].query_id == "OFF"
    assert off_cands[0].gtin_on_page == spec.gtin
    assert [h.query_id for h in res.health if h.provider == "off"] == ["OFF"]


def test_lookup_skipped_without_a_valid_gtin():
    off = LookupStub(threading.Event(), threading.Event())
    res = retrieve(spec_for(barcode="6281007035225"), [off, Stub("serper")])
    assert off.calls == 0
    assert all(h.provider != "off" for h in res.health)


def test_bing_fallback_only_when_every_primary_is_down():
    spec = spec_for(barcode=VALID_EAN)                  # plan: Q1, Q3 (serper only), Q4
    plan = build_queries(spec)
    assert [q.query_id for q in plan] == ["Q1", "Q3", "Q4"]

    serper_down = Stub("serper", status="quota")
    bing = Stub("bing_html", [cand("https://img.example.com/bing.jpg", sanctioned=True)],
                sanctioned=False, fallback=True)
    res = retrieve(spec, [serper_down, bing])
    assert serper_down.calls == [plan[0].text]          # quota: not asked again for this SKU
    assert bing.calls == [plan[0].text, plan[2].text]   # Q3 is Serper-only, so it was skipped
    assert res.queries == [plan[0].text, plan[2].text]  # a skipped query does not use the budget
    assert all(c.sanctioned is False for c in res.pool)  # a scraper cannot emit sanctioned candidates

    serper_ok = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])
    bing2 = Stub("bing_html", [cand("https://img.example.com/bing.jpg")], sanctioned=False, fallback=True)
    retrieve(spec, [serper_ok, bing2])
    assert bing2.calls == []

    serper_empty = Stub("serper", [])                   # healthy but empty: no scraping
    bing3 = Stub("bing_html", [cand("https://img.example.com/bing.jpg")], sanctioned=False, fallback=True)
    retrieve(spec, [serper_empty, bing3])
    assert bing3.calls == []


def test_primary_bing_never_gets_the_serper_only_query():
    spec = spec_for()
    bing = Stub("bing_html", [cand("https://img.example.com/bing.jpg")], sanctioned=False)
    res = retrieve(spec, [bing])
    assert all("site:" not in q for q in bing.calls)
    assert res.queries == bing.calls


def test_query_budget():
    spec = spec_for(name_ar="حليب المراعي كامل الدسم 1 لتر", barcode=VALID_EAN)
    serper = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])
    res = retrieve(spec, [serper], max_queries=2)
    plan = build_queries(spec)
    assert serper.calls == [plan[0].text, plan[1].text]
    assert len(res.queries) == 2


def test_relaxations_only_when_asked_and_within_budget():
    spec = spec_for()                                   # plan: Q1, Q3
    serper = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])
    res = retrieve(spec, [serper])
    assert res.relaxed_ids == set()
    assert len(serper.calls) == 2

    per_query = {}
    serper2 = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")], per_query=per_query)
    r = Retriever(spec, [serper2])
    r.run()
    from catalog_match.query_plan import relaxations
    rel = relaxations(spec)
    per_query[rel[0].text] = [cand("https://cdn.example.com/p/relaxed-only.jpg")]
    r.relax()
    assert r.result.relaxed_ids == {"R1", "R2"}
    assert serper2.calls[-2:] == [q.text for q in rel]
    relaxed_only = next(c for c in r.result.pool if "relaxed-only" in c.image_url)
    assert relaxed_only.query_id == "R1"
    shared = next(c for c in r.result.pool if c.image_url.endswith("/a.jpg"))
    assert shared.query_id == "Q1"                      # first found by a strict query

    # relax_when drives it from retrieve(); the budget is shared with the plan.
    serper3 = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])
    res3 = retrieve(spec, [serper3], max_queries=3, relax_when=lambda pool: True)
    assert res3.relaxed_ids == {"R1"}
    assert len(res3.queries) == 3

    # Never with a staff custom query.
    serper4 = Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])
    res4 = retrieve(spec, [serper4], custom_query="Almarai Full Cream Milk 1L site:carrefouruae.com",
                    relax_when=lambda pool: True)
    assert serper4.calls == ["Almarai Full Cream Milk 1L site:carrefouruae.com"]
    assert res4.relaxed_ids == set()


def test_sanctioned_duplicate_upgrades_a_scraped_candidate():
    spec = spec_for()
    scraped = Stub("bing_html", [cand("https://cdn.example.com/p/milk.jpg?width=300")], sanctioned=False)
    api = Stub("serper", [cand("https://cdn.example.com/p/milk.jpg", page_url=CARREFOUR_PAGE,
                               page_title="Almarai Fresh Milk Full Fat 1L", width=1200, height=1200)])
    res = retrieve(spec, [scraped, api], max_queries=1)
    assert len(res.pool) == 1
    c = res.pool[0]
    assert c.sanctioned is True and c.provider == "serper"
    assert c.image_url == "https://cdn.example.com/p/milk.jpg"
    assert (c.width, c.height) == (1200, 1200)
    assert c.consensus_count == 2


def test_garbage_from_a_provider_is_an_error_not_a_crash():
    class Garbage:
        name, kind, sanctioned, fallback = "garbage", "search", True, False

        def search(self, query, hl, spec):
            return {"images": []}

    res = retrieve(spec_for(), [Garbage(), Stub("serper", [cand("https://cdn.example.com/p/a.jpg")])])
    assert {h.status for h in res.health if h.provider == "garbage"} == {"error"}
    assert len(res.pool) == 1


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def test_norm_image_url():
    base = norm_image_url("https://cdn.example.com/p/Milk-1L.jpg")
    assert base == "cdn.example.com/p/milk-1l.jpg"
    for variant in ("https://cdn.example.com/p/Milk-1L.jpg?w=800",
                    "http://www.cdn.example.com/p/milk-1l.jpg?width=800&height=800&fit=crop&q=80",
                    "https://cdn.example.com/p/milk-1l.jpg?v=1699999999#zoom",
                    "//cdn.example.com/p/milk-1l.jpg/"):
        assert norm_image_url(variant) == base, variant
    # An extension-less endpoint is identified by its query: different images stay different.
    a = norm_image_url("https://shop.example.com/_next/image?url=%2Fimg%2Fa.jpg&w=640&q=75")
    b = norm_image_url("https://shop.example.com/_next/image?url=%2Fimg%2Fb.jpg&w=640&q=75")
    assert a != b
    assert a == norm_image_url("https://shop.example.com/_next/image?w=1080&url=%2Fimg%2Fa.jpg")
    assert norm_image_url("") == ""


# ---------------------------------------------------------------------------
# default_providers()
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    for name in ("SERPER_API_KEY", "ENABLE_BING_HTML_FALLBACK", "GOOGLE_SEARCH_API_KEYS", "GOOGLE_SEARCH_API_KEY",
                 "GOOGLE_SEARCH_CX_LIST", "GOOGLE_SEARCH_CX", "CSE_SUNSET_DATE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def describe(providers):
    return [(p.name, p.fallback) for p in providers]


def test_default_providers_without_keys_uses_bing_as_primary(clean_settings):
    assert describe(default_providers()) == [("off", False), ("bing_html", False)]


def test_default_providers_with_serper(clean_settings):
    clean_settings.setenv("SERPER_API_KEY", "k")
    assert describe(default_providers()) == [("serper", False), ("off", False), ("bing_html", True)]
    clean_settings.setenv("ENABLE_BING_HTML_FALLBACK", "false")
    assert describe(default_providers()) == [("serper", False), ("off", False)]


def test_default_providers_cse_until_sunset(clean_settings):
    clean_settings.setenv("GOOGLE_SEARCH_API_KEY", "legacy-key")
    clean_settings.setenv("GOOGLE_SEARCH_CX", "legacy-cx")
    names = [p.name for p in default_providers(today=dt.date(2026, 9, 30))]
    assert "cse_legacy" in names
    assert ("bing_html", True) in describe(default_providers(today=dt.date(2026, 9, 30)))
    names_after = [p.name for p in default_providers(today=dt.date(2027, 1, 1))]
    assert "cse_legacy" not in names_after
    assert ("bing_html", False) in describe(default_providers(today=dt.date(2027, 1, 1)))
