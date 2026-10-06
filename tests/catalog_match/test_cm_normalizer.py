"""catalog_match.normalizer: a cheap model reads an abbreviated sheet name, for the search queries only.

Fakes stand in for the model everywhere (no socket is opened but the database test's). Covered here: the reading and
its cleaning, the cache (memo, database, prompt version), the per-run budget and the spend rows, and the silent
fallback (no key, the breakers, a timeout, an HTTP error, a bad reply). What the search does with a reading:
test_cm_normalizer_search.py.
"""

import json
import socket

import pytest
import requests

from catalog_match import brand_discovery as bd
from catalog_match import cassette, normalizer as nz, settings
from catalog_match.identity import build_sku_spec
from catalog_match.verifiers.spend import MemorySpendStore


@pytest.fixture(autouse=True)
def _offline(monkeypatch, request):
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    if "mariadb_or_skip" not in request.fixturenames:     # the database test needs its local socket
        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    bd.forget_all()
    nz.start_run()
    nz.MEMO.clear()
    from catalog_match import verify
    verify.BREAKER.reset()
    yield
    bd.forget_all()
    verify.BREAKER.reset()


def _key():
    return "-".join(("tst", "gem", "7f3c9a1e"))


SUPT = build_sku_spec({"name": "SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "brand": "SUP/T"}, {})
GOLD = build_sku_spec({"name": "AMERICAN GOLD LGT MEAT TUNA FLAKE IN SUNFLOWER OIL 160GM", "brand": "AMERICAN GOLD"}, {})
PRAWNS = build_sku_spec({"name": "SQ SALITED DRY PRAWNS FISF", "brand": "SQ SALITED"}, {})
MAPPED = {"almarai": {"brand": "Almarai", "synonyms": ["Almarai", "ALMARAI"], "excluded_competitors": []}}
MILK = build_sku_spec({"name": "ALMARAI FF MILK 1L", "brand": "ALMARAI"}, MAPPED)

READINGS = {
    "SUP/T": {"brand": "Super Tasty", "product_type": "tuna", "variant": "white meat solid in salt water",
              "size": "185g", "pack": "", "expanded_name": "Super Tasty White Meat Solid Tuna in Salt Water 185g",
              "confidence": 0.9},
    "AMERICAN GOLD": {"brand": "American Gold", "product_type": "tuna", "variant": "light meat flakes sunflower oil",
                      "size": "160g", "pack": "",
                      "expanded_name": "American Gold Light Meat Tuna Flakes in Sunflower Oil 160g", "confidence": 0.8},
    "SQ SALITED": {"brand": "SQ", "product_type": "dried prawns", "variant": "salted", "size": "", "pack": "",
                   "expanded_name": "SQ Salted Dried Prawns Fish", "confidence": 0.7},
    "ALMARAI": {"brand": "Nestle", "product_type": "milk", "variant": "full fat", "size": "1L", "pack": "",
                "expanded_name": "Nestle Almarai Full Fat Milk 1L", "confidence": 0.9},
}


class FakeClient:
    """The model: answers READINGS by the brand cell of the prompt, counts calls, bills 300 / 60 tokens."""

    model = nz.MODEL

    def __init__(self, fail=None, reading=None):
        self.prompts, self.fail, self.reading = [], fail, reading
        self.api_key = _key()

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.fail is not None:
            raise self.fail
        usage = {"provider": "gemini", "model": self.model, "input_tokens": 300, "output_tokens": 60,
                 "estimated": False}
        if self.reading is not None:
            return dict(self.reading), usage
        for brand, reading in READINGS.items():
            if f'Sheet brand column: "{brand}"' in prompt:
                return dict(reading), usage
        return {"brand": "", "expanded_name": "", "confidence": 0}, usage


def normaliser(client=None, **kw):
    kw.setdefault("budget_usd", 1.0)
    return nz.QueryNormalizer(client=client or FakeClient(), cache=kw.pop("cache", nz.MemoryNormalizerCache()),
                              spend_store=kw.pop("spend_store", MemorySpendStore()), **kw)


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------

def test_a_reading_is_cleaned_for_a_search_query():
    value = nz.parse_fields({"brand": ' "Super Tasty" ', "product_type": "tuna\n", "variant": "",
                             "size": "185g", "pack": 3, "confidence": "0.85",
                             "expanded_name": "site:noon.com Super Tasty -Tuna \"White\" intitle:Meat " + "x" * 300})
    assert value.brand == "Super Tasty" and value.pack == "3" and value.confidence == 0.85
    assert "site:" not in value.expanded_name and "intitle:" not in value.expanded_name
    assert '"' not in value.expanded_name and " -" not in value.expanded_name
    assert value.expanded_name.startswith("noon.com Super Tasty Tuna White Meat")
    assert len(value.expanded_name) <= 160
    assert nz.parse_fields({"expanded_name": "", "brand": "X"}) is None
    assert nz.parse_fields(["not", "an", "object"]) is None
    assert nz.parse_fields({"expanded_name": "Tuna", "confidence": "high"}).confidence == 0.0
    assert nz.parse_fields({"expanded_name": "Tuna", "confidence": 7}).confidence == 1.0


def test_the_prompt_quotes_the_sheet_and_asks_for_every_field():
    prompt = nz.build_prompt("SUP/T WT/MEAT  SOLIDTUNA", "SUP/T")
    assert 'Sheet product name: "SUP/T WT/MEAT SOLIDTUNA"' in prompt and 'Sheet brand column: "SUP/T"' in prompt
    assert "Sheet brand column: (empty)" in nz.build_prompt("TUNA", "")
    for name in nz.FIELDS:
        assert f"- {name}:" in prompt
    assert nz.RESPONSE_SCHEMA["required"] == list(nz.FIELDS)


def test_the_cache_key_is_the_normalised_input_and_the_prompt_version():
    assert nz.cache_key("SUP/T  WT/MEAT", "sup/t") == nz.cache_key("sup/t wt/meat", "SUP/T ")
    assert nz.cache_key("SUP/T WT/MEAT", "SUP/T") != nz.cache_key("SUP/T WT/MEAT", "")
    assert nz.cache_key("SUP/T WT/MEAT", "SUP/T") != nz.cache_key("SUP/T WT/MEAT", "SUP/T", prompt_version="qn0")


# ---------------------------------------------------------------------------
# Caching: each product is paid once
# ---------------------------------------------------------------------------

def test_each_product_is_paid_once_memo_then_database():
    client, cache, spend = FakeClient(), nz.MemoryNormalizerCache(), MemorySpendStore()
    first = normaliser(client, cache=cache, spend_store=spend)
    reading = first.normalize(SUPT)
    assert reading.status == "ok" and reading.value.brand == "Super Tasty" and len(client.prompts) == 1
    assert reading.usage["role"] == "normalizer" and reading.usage["usd"] > 0
    again = first.normalize(build_sku_spec({"name": "sup/t  wt/meat solidtuna saltwater 185gm", "brand": "Sup/T"}, {}))
    assert again.status == "cache" and again.usage is None and len(client.prompts) == 1     # the memo
    # another process (a fresh memo) reads the database row: still one call
    other = normaliser(client, cache=cache, spend_store=spend)
    assert other.normalize(SUPT).status == "cache" and len(client.prompts) == 1
    row = next(iter(cache.rows.values()))
    assert row["prompt_version"] == nz.PROMPT_VERSION and row["input_name"] == SUPT.raw_name
    assert row["result"]["expanded_name"].startswith("Super Tasty")
    assert len(spend.added) == 1 and spend.added[0]["role"] == "normalizer"


def test_a_new_prompt_version_is_paid_again(monkeypatch):
    client, cache = FakeClient(), nz.MemoryNormalizerCache()
    normaliser(client, cache=cache).normalize(SUPT)
    monkeypatch.setattr(nz, "PROMPT_VERSION", "qn-next")
    assert normaliser(client, cache=cache).normalize(SUPT).status == "ok" and len(client.prompts) == 2


def test_an_unreadable_cache_asks_the_model_and_a_failed_answer_is_not_cached():
    client = FakeClient()
    assert normaliser(client, cache=nz.MemoryNormalizerCache(fail_reads=True)).normalize(SUPT).status == "ok"
    cache = nz.MemoryNormalizerCache()
    assert normaliser(FakeClient(fail=nz.NormalizerError("http_503")), cache=cache).normalize(SUPT).status == "http_503"
    assert cache.rows == {}


def test_a_recorded_dry_run_skips_the_database_cache(monkeypatch):
    cache = nz.MemoryNormalizerCache()
    monkeypatch.setattr(cassette, "active", lambda: object())
    assert normaliser(FakeClient(), cache=cache).normalize(SUPT).status == "ok"
    assert cache.reads == 0


def test_an_arabic_sheet_name_and_an_empty_one_make_no_call():
    client = FakeClient()
    arabic = build_sku_spec({"name": "تونة قطع خفيفة 185 غرام", "brand": "سوبر تيستي"}, {})
    assert normaliser(client).normalize(arabic).status == "skipped"
    assert normaliser(client).normalize(build_sku_spec({"name": "", "brand": "X"}, {})).status == "skipped"
    assert client.prompts == []


# ---------------------------------------------------------------------------
# The run budget and the spend table
# ---------------------------------------------------------------------------

def test_the_run_budget_caps_the_calls_and_start_run_restores_it():
    spend, budget = MemorySpendStore(), nz.RunBudget()
    client = FakeClient()
    one_call = nz.usd_of(nz.MODEL, 300, 60)                         # what the fake model bills
    estimate = nz.usd_of(nz.MODEL, *nz.estimate_tokens(nz.build_prompt(SUPT.raw_name, SUPT.brand_raw)))
    norm = normaliser(client, spend_store=spend, budget=budget, budget_usd=estimate * 1.2)
    assert norm.normalize(SUPT).status == "ok"
    assert norm.normalize(GOLD).status == "budget"           # the next call's estimate would pass the cap
    assert len(client.prompts) == 1 and budget.calls == 1
    assert budget.spent == pytest.approx(one_call)
    assert spend.month_rows()[0]["role"] == "normalizer" and spend.month_rows()[0]["calls"] == 1
    budget.reset()
    assert norm.normalize(GOLD).status == "ok"


def test_a_zero_budget_makes_no_call():
    client = FakeClient()
    assert normaliser(client, budget_usd=0.0).normalize(SUPT).status == "budget" and client.prompts == []


def test_the_default_normaliser_shares_the_run_budget(monkeypatch):
    monkeypatch.setenv("QUERY_NORMALIZER_RUN_BUDGET_USD", "0.25")
    default = nz.default_normalizer()
    assert default.budget is nz.RUN and default.breaker is nz.BREAKER and default.memo is nz.MEMO
    assert default.budget_usd() == 0.25
    nz.RUN.add(0.3)
    assert default.normalize(SUPT).status in ("budget", "no_key")
    nz.start_run()
    assert nz.RUN.spent == 0.0


def test_the_estimate_is_about_two_hundredths_of_a_cent():
    usd = nz.estimate_usd()
    assert 0.00005 < usd < 0.0004
    tin, tout = nz.estimate_tokens(nz.build_prompt(GOLD.raw_name, GOLD.brand_raw))
    assert 250 < tin < 600 and tout == nz.OUTPUT_TOKENS_ESTIMATE


# ---------------------------------------------------------------------------
# Failure: skipped silently
# ---------------------------------------------------------------------------

def test_no_key_and_the_label_readers_breaker_make_no_call():
    client = FakeClient()
    client.api_key = ""
    assert normaliser(client).normalize(SUPT).status == "no_key" and client.prompts == []
    from catalog_match import verify
    for _ in range(verify.BREAKER_THRESHOLD):
        verify.BREAKER.record(False)
    client = FakeClient()
    assert normaliser(client).normalize(SUPT).status == "circuit_open" and client.prompts == []


def test_three_failures_in_a_row_open_the_breaker_until_the_next_run():
    client = FakeClient(fail=nz.NormalizerError("connection_error"))
    breaker = nz.Breaker()
    norm = normaliser(client, breaker=breaker)
    statuses = [norm.normalize(s).status for s in (SUPT, GOLD, PRAWNS, MILK)]
    assert statuses == ["connection_error"] * 3 + ["circuit_open"] and len(client.prompts) == 3
    breaker.reset()
    client.fail = None
    assert norm.normalize(MILK).status == "ok"


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _reply(fields, usage=True):
    payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(fields)}]}, "finishReason": "STOP"}]}
    if usage:
        payload["usageMetadata"] = {"promptTokenCount": 312, "candidatesTokenCount": 71}
    return _Resp(200, payload)


def test_the_gemini_client_sends_one_structured_call_with_the_key_in_a_header():
    session = _Session(_reply(READINGS["SUP/T"]))
    client = nz.GeminiNormalizerClient(api_key=_key(), session=session)
    data, usage = client.generate(nz.build_prompt(SUPT.raw_name, SUPT.brand_raw))
    call = session.calls[0]
    assert call["url"] == f"{nz.API_ROOT}/{nz.MODEL}:generateContent" and _key() not in call["url"]
    assert call["headers"]["x-goog-api-key"] == _key() and call["timeout"] == nz.TIMEOUT_S == 3.0
    config = call["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["responseSchema"] == nz.RESPONSE_SCHEMA
    assert data["brand"] == "Super Tasty"
    assert (usage["input_tokens"], usage["output_tokens"], usage["estimated"]) == (312, 71, False)


@pytest.mark.parametrize("answer,code", [
    (requests.Timeout("slow"), "timeout"),
    (requests.ConnectionError("down"), "connection_error"),
    (_Resp(503, {}), "http_503"),
    (_Resp(429, {}), "http_429"),
    (_Resp(200, ValueError("not json")), "bad_reply"),
    (_Resp(200, {"candidates": []}), "bad_reply"),
    (_Resp(200, {"candidates": [{"content": {"parts": [{"text": "no json here"}]}}]}), "bad_reply"),
])
def test_every_failure_of_the_call_is_a_status_never_a_crash(answer, code):
    client = nz.GeminiNormalizerClient(api_key=_key(), session=_Session(answer))
    spend = MemorySpendStore()
    reading = normaliser(client, spend_store=spend).normalize(SUPT)
    assert reading.status == code and reading.value is None
    if code == "timeout":           # sent and never answered: most likely billed, so it counts (estimated)
        assert reading.usage["timed_out"] and reading.usage["estimated"] and len(spend.added) == 1
    if code in ("connection_error", "http_503", "http_429"):
        assert reading.usage is None and spend.added == []


def test_a_low_confidence_reading_writes_no_query():
    low = dict(READINGS["AMERICAN GOLD"], confidence=0.3)
    reading = normaliser(FakeClient(reading=low)).normalize(GOLD)
    assert reading.status == "ok" and nz.hint_of(reading) is None
    assert nz.hint_of(None) is None


def test_resolve_follows_the_setting_and_the_injected_stages(monkeypatch):
    monkeypatch.setenv("QUERY_NORMALIZER", "gemini")
    assert settings.query_normalizer() == "gemini"
    assert isinstance(nz.resolve(None, injected=False), nz.QueryNormalizer)
    assert nz.resolve(None, injected=True) is None                 # the evaluation, the dry run, tests
    assert isinstance(nz.resolve(True, injected=True), nz.QueryNormalizer)
    assert nz.resolve(False) is None
    fake = object()
    assert nz.resolve(fake, injected=True) is fake
    monkeypatch.setenv("QUERY_NORMALIZER", "off")
    assert nz.resolve(None) is None and nz.resolve(True) is None
    monkeypatch.setenv("QUERY_NORMALIZER", "claude")
    assert settings.query_normalizer() == "off"                    # unknown: never a surprise paid call
    monkeypatch.delenv("QUERY_NORMALIZER")
    assert settings.DEFAULTS["QUERY_NORMALIZER"] == "gemini" and settings.query_normalizer() == "gemini"
    monkeypatch.setenv("QUERY_NORMALIZER_RUN_BUDGET_USD", "lots")
    assert settings.query_normalizer_run_budget_usd() == 0.5


# ---------------------------------------------------------------------------
# The database cache (MariaDB)
# ---------------------------------------------------------------------------

def test_the_database_cache_round_trip(mariadb_or_skip, monkeypatch):
    db = mariadb_or_skip
    conn = db.get_db_connection()
    try:
        conn.cursor().execute(f"DROP TABLE IF EXISTS {nz.TABLE}")
        conn.commit()
    finally:
        conn.close()
    nz.MariaDbNormalizerCache._ready_for.clear()
    cache = nz.MariaDbNormalizerCache()
    key = nz.cache_key(SUPT.raw_name, SUPT.brand_raw)
    assert cache.get(key) is None                                     # the table does not exist yet: a miss
    client = FakeClient()
    assert normaliser(client, cache=cache).normalize(SUPT).status == "ok"
    assert cache.get(key)["expanded_name"].startswith("Super Tasty")
    nz.MEMO.clear()
    assert normaliser(client, cache=cache, memo=nz._Memo()).normalize(SUPT).status == "cache"
    assert len(client.prompts) == 1

