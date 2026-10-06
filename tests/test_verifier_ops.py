"""The verifier package outside the search: settings loading, the month spend table, and the health report.

* config.load_db_config() reads the verifier settings the dashboard writes (system_settings);
* catalog_match.verifiers.spend.MariaDbSpendStore round-trips against the test database;
* ops_health prices verifier calls per model from outcome.vlm_usage (old rows keep the flat Gemini price),
  raises the budget / key alerts, and the health page script shows one cost line per model.
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ops_health

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
NODE = shutil.which("node")
HEALTH_JS = Path(__file__).resolve().parents[1] / "dashboard" / "public" / "js" / "health.js"


def _row(age_s, outcome, failure_code=None):
    return {"status": "ready_for_review", "failure_code": failure_code, "has_trace": 1, "age_s": age_s,
            "outcome_json": json.dumps(outcome)}


def _outcome(vlm_calls=0, usage=None, notices=None, decision="REVIEW_PRESELECTED", failure_code=None):
    doc = {"decision": decision, "failure_code": failure_code,
           "provider_health": [{"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1"}],
           "queries": ["q"], "sku_key": "k", "vlm_calls": vlm_calls}
    if usage is not None:
        doc["vlm_usage"] = usage
    if notices is not None:
        doc["verifier_notices"] = notices
    return doc


def _use(role, provider, model, usd, tin=4000, tout=500, estimated=False):
    return {"role": role, "provider": provider, "model": model, "input_tokens": tin, "output_tokens": tout,
            "usd": usd, "estimated": estimated, "images": 1}


PRIMARY = _use("primary", "gemini", "gemini-3.1-flash-lite", 0.002)
STRONG = _use("strong", "claude", "claude-sonnet-5-5", 0.015, tin=1500, tout=700)


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------

def test_config_reads_the_verifier_settings(monkeypatch, fake_connection):
    import config
    import pymysql

    for name in list(config.VERIFIER_DB_KEYS.values()):
        monkeypatch.setattr(config, name, getattr(config, name))
    monkeypatch.setattr(config, "VERIFIER_STRONG", "gemini:gemini-3.5-flash")
    rows = [{"key": "anthropic_api_key", "value": "sk-ant-from-db"}, {"key": "verifier_primary", "value": "claude:claude-haiku-4-5"},
            {"key": "verifier_strong", "value": ""}, {"key": "verifier_monthly_budget_usd", "value": "7.5"},
            {"key": "model_prices", "value": '{"claude:claude-haiku-4-5": {"input": 1, "output": 5}}'}]

    def responder(sql, params):
        return [{"t": "system_settings"}] if sql.upper().startswith("SHOW") else rows

    monkeypatch.setattr(pymysql, "connect", lambda **k: fake_connection(responder))
    config.load_db_config()
    assert config.ANTHROPIC_API_KEY == "sk-ant-from-db" and config.VERIFIER_PRIMARY == "claude:claude-haiku-4-5"
    assert config.VERIFIER_STRONG == "gemini:gemini-3.5-flash"        # an empty stored value keeps .env / default
    assert config.VERIFIER_MONTHLY_BUDGET_USD == "7.5" and "claude-haiku-4-5" in config.MODEL_PRICES

    from catalog_match import settings
    monkeypatch.setattr(settings, "_config", config)
    assert settings.verifier_primary() == "claude:claude-haiku-4-5" and settings.verifier_monthly_budget_usd() == 7.5
    assert settings.anthropic_api_key() == "sk-ant-from-db"
    monkeypatch.setattr(config, "VERIFIER_MONTHLY_BUDGET_USD", "lots")
    assert settings.verifier_monthly_budget_usd() == 5.0                 # unreadable: the default, never unlimited


# ---------------------------------------------------------------------------
# Spend table (MariaDB)
# ---------------------------------------------------------------------------

def test_spend_store_round_trip_and_month_report(mariadb_or_skip, monkeypatch):
    from catalog_match import settings
    from catalog_match.verifiers.spend import MariaDbSpendStore, current_month

    db = mariadb_or_skip
    conn = db.get_db_connection()
    try:
        conn.cursor().execute("DROP TABLE IF EXISTS verifier_spend")
        conn.commit()
    finally:
        conn.close()
    MariaDbSpendStore._ready_for.clear()
    store = MariaDbSpendStore()
    assert store.role_spend("strong") == 0.0 and store.month_rows() == []        # no table yet: nothing spent
    conn = db.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'verifier_spend'")
        assert cursor.fetchone(), "the first budget read creates the table the spend will be counted in"
    finally:
        conn.close()
    assert store.add(STRONG) and store.add(STRONG) and store.add(PRIMARY)
    assert store.add(dict(STRONG, usd=1.0), month="2026-01")                    # another month
    assert store.role_spend("strong") == pytest.approx(0.03)
    rows = store.month_rows()
    assert [(r["role"], r["model"], r["calls"]) for r in rows] == [("strong", "claude-sonnet-5-5", 2),
                                                                     ("primary", "gemini-3.1-flash-lite", 1)]
    assert rows[0]["input_tokens"] == 3000 and current_month() != "2026-01"

    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("VERIFIER_MONTHLY_BUDGET_USD", "5")
    month = ops_health.verifier_month()
    assert month["month"] == current_month() and month["budget_usd"] == 5.0
    assert month["strong_usd"] == pytest.approx(0.03) and month["total_usd"] == pytest.approx(0.032)


def test_spend_store_never_breaks_a_search_when_the_database_is_down():
    from catalog_match.verifiers.spend import MariaDbSpendStore

    def down():
        raise OSError("database down")

    store = MariaDbSpendStore(connect=down)
    assert store.add(STRONG) is False
    with pytest.raises(OSError):
        store.role_spend("strong")                   # the cascade turns this into 'strong_budget_unknown'


def test_a_spend_table_that_cannot_be_created_is_an_unknown_spend_not_a_zero_one(fake_connection):
    """A database user without CREATE: no row can ever be counted, so 'no table = nothing spent' would let the
    strong model spend without limit. The budget read must fail (no second look), not return 0."""
    import pymysql
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import Candidate, FetchedImage, VerificationResult
    from catalog_match.verifiers import registry
    from catalog_match.verifiers.cascade import CascadeVerifier
    from catalog_match.verifiers.spend import MariaDbSpendStore
    from catalog_match.verify import make_verdict

    def responder(sql, params):
        if sql.upper().startswith(("SELECT", "INSERT")):
            raise pymysql.err.ProgrammingError(1146, "Table 'automation_db.verifier_spend' doesn't exist")
        if sql.upper().startswith("CREATE"):
            raise pymysql.err.OperationalError(1142, "CREATE command denied to user 'app'@'localhost'")
        return None

    conns = []

    def connect():
        conns.append(fake_connection(responder))
        return conns[-1]

    store = MariaDbSpendStore(connect=connect)
    with pytest.raises(pymysql.err.OperationalError):
        store.role_spend("strong")
    assert store.add(STRONG) is False                # the write fails the same way, and never raises

    spec = build_sku_spec({"name": "Al Rawabi Laban Up 180ml", "brand": "Al Rawabi"}, {})
    unsure = {"image_index": 1, "brand_text": "Al Rawabi", "variant_text": "Laban Up", "size_text": "",
              "pack_count": 1, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
              "size_match": "unsure"}
    strong_calls = []

    class Primary:
        provider, model = "gemini", "gemini-3.1-flash-lite"

        def verify(self, spec_, images):
            return VerificationResult(status="ok", calls=1, verdicts=[make_verdict(spec_, 0, unsure)])

    class Strong:
        provider, model, long_side = "gemini", "gemini-3.5-flash", 1568

        def verify(self, spec_, images):
            strong_calls.append(len(images))
            return VerificationResult(status="ok", calls=1, verdicts=[make_verdict(spec_, 0, dict(unsure, size_text="180 ml",
                                                                                                size_match="yes"))])

    cascade = CascadeVerifier(Primary(), Strong(), strong_ref=registry.parse_model_id("gemini:gemini-3.5-flash"),
                              spend_store=store, tier_of=lambda s, f: 1)
    image = FetchedImage(candidate=Candidate(image_url="https://a.ae/1.jpg"), ok=True, width=800, height=800)
    result = cascade.verify(spec, [image])
    assert strong_calls == [] and result.notices == ["strong_budget_unknown"]
    assert result.verdicts[0].decision == "UNSURE"


# ---------------------------------------------------------------------------
# ops_health
# ---------------------------------------------------------------------------

def test_cost_is_priced_per_model_and_old_rows_keep_the_flat_price():
    rows = [_row(60, _outcome(2, [PRIMARY, STRONG])),
            _row(120, _outcome(1, [PRIMARY])),
            _row(180, _outcome(1)),                                   # an older trace without usage: $0.001 per call
            _row(240, _outcome(2, [PRIMARY, {"provider": "", "model": "x"}, "junk", dict(STRONG, usd="NaN")]))]
    day = ops_health.summarize(rows, now=NOW)["windows"]["24h"]
    models = {(m["role"], m["model"]): m for m in day["verifier_models"]}
    assert models[("primary", "gemini-3.1-flash-lite")]["calls"] == 3
    assert models[("strong", "claude-sonnet-5-5")]["calls"] == 2
    assert models[("strong", "claude-sonnet-5-5")]["usd"] == pytest.approx(0.015)      # a NaN price counts 0
    assert [m["role"] for m in day["verifier_models"]] == ["primary", "strong"]
    assert day["cost_usd"]["gemini"] == pytest.approx(0.006 + 0.001)
    assert day["cost_usd"]["claude"] == pytest.approx(0.015)
    assert day["cost_usd"]["total"] == pytest.approx(day["cost_usd"]["serper"] + 0.007 + 0.015)
    assert day["verifier"]["calls"] == 6
    # rows without usage keep the old shape: no claude key
    old = ops_health.summarize([_row(60, _outcome(1))], now=NOW)["windows"]["24h"]
    assert set(old["cost_usd"]) == {"serper", "gemini", "total"} and old["verifier_models"] == []


def test_budget_and_key_alerts():
    budget = [_row(60, _outcome(1, [PRIMARY], ["strong_budget_exhausted"]))]
    alerts = ops_health.summarize(budget, now=NOW)["alerts"]
    assert [a["code"] for a in alerts] == ["VERIFIER_BUDGET"]
    assert "ميزانية" in alerts[0]["message"] and "VERIFIER" not in alerts[0]["message"]
    # a later strong look (the owner raised the budget) clears it
    raised = [_row(30, _outcome(2, [PRIMARY, STRONG]))] + budget
    assert ops_health.summarize(raised, now=NOW)["alerts"] == []
    # a budget of 0 is the owner's «no second look»: no "the month's budget ran out, wait for next month" alert
    zero = [_row(60 * i, _outcome(1, [PRIMARY], ["strong_budget_zero"])) for i in (1, 2, 3)]
    assert ops_health.summarize(zero, now=NOW)["alerts"] == []
    key = [_row(60 * i, _outcome(1, [PRIMARY], ["strong:claude_key_rejected"])) for i in (1, 2)]
    assert [a["code"] for a in ops_health.summarize(key, now=NOW)["alerts"]] == ["VERIFIER_KEY"]
    assert ops_health.summarize(key[:1], now=NOW)["alerts"] == []        # one search is not enough
    notice = ops_health.alerts([ops_health.entry_from_row(r, NOW) for r in key])
    assert notice and notice[0]["searches"] == 2
    # a Claude PRIMARY that reads fine does not hide the strong model's rejected key
    claude_primary = _use("primary", "claude", "claude-haiku-4-5", 0.004)
    key = [_row(60 * i, _outcome(1, [claude_primary], ["strong:gemini_key_rejected"])) for i in (1, 2)]
    assert [a["code"] for a in ops_health.summarize(key, now=NOW)["alerts"]] == ["VERIFIER_KEY"]


def test_a_rejected_primary_key_is_not_blamed_on_the_strong_model():
    """The PRIMARY reader's key problem makes every search VERIFIER_DOWN (GEMINI_DOWN alert). The strong-model key
    alert says «add the key or turn the strong model off», which cannot help there, so it must stay quiet."""
    for notice in ("gemini_key_rejected", "claude_key_missing", "claude_key_rejected"):
        rows = [_row(60 * i, _outcome(1, [], [notice], decision="REVIEW_PRESELECTED", failure_code="VERIFIER_DOWN"),
                     failure_code="VERIFIER_DOWN") for i in (1, 2, 3)]
        assert [a["code"] for a in ops_health.summarize(rows, now=NOW)["alerts"]] == ["GEMINI_DOWN"], notice


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_health_page_shows_one_cost_line_per_model():
    rows = [_row(60, _outcome(2, [PRIMARY, STRONG])), _row(120, _outcome(1))]
    report = dict(ops_health.summarize(rows, now=NOW), status="success",
                  verifier_month={"month": "2026-10", "budget_usd": 5.0, "strong_usd": 0.42, "total_usd": 0.5})
    harness = ("globalThis.window = globalThis;\n" + HEALTH_JS.read_text(encoding="utf-8")
               + f"\nconsole.log(JSON.stringify(window.LaqtaHealth.opsView({json.dumps(report)}, '24h').cost));")
    out = subprocess.run([NODE, "-"], input=harness, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    cost = json.loads(out.stdout.strip().splitlines()[-1])
    labels = [line["label"] for line in cost["lines"]]
    assert labels[0].startswith("Serper")
    assert "Gemini 3.1 Flash-Lite · فحص واحد" in labels
    assert "Claude Sonnet 5.5 · نظرة تانية · فحص واحد" in labels
    assert "Gemini · قراءات أقدم بلا تفاصيل" in labels                  # the old row's flat-priced call
    assert "النموذج القوي هالشهر" in cost["note"] and "نماذج التحقق" in cost["note"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_health_page_names_the_expansion_sources_and_prices_serpapi_on_its_own_line():
    """The expansion round's paid calls (catalog_match/expand.py) show under their own names, and SerpApi Lens gets
    its own cost line at its own price; the total is ops_health's."""
    doc = _outcome(1, [PRIMARY])
    doc["provider_health"] += [{"provider": p, "status": s, "http_status": 200, "query_id": q} for p, s, q in
                               [("serper_web", "ok", "X1"), ("serper_shopping", "empty", "X2"),
                                ("lens_serper", "quota", "X3"), ("lens_serpapi", "ok", "X4")]]
    report = dict(ops_health.summarize([_row(60, doc), _row(120, _outcome(1, [PRIMARY]))], now=NOW), status="success")
    window = report["windows"]["24h"]
    assert window["serper_queries"] == 4 and window["sources"]["lens_serpapi"]["calls"] == 1
    harness = ("globalThis.window = globalThis;\n" + HEALTH_JS.read_text(encoding="utf-8")
               + f"\nconsole.log(JSON.stringify(window.LaqtaHealth.opsView({json.dumps(report)}, '24h')));")
    out = subprocess.run([NODE, "-"], input=harness, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    view = json.loads(out.stdout.strip().splitlines()[-1])
    names = [p["name"] for p in view["providers"]]
    assert names[0] == "Serper (Google)"
    assert {"Serper · صفحات المتاجر", "Serper · Google Shopping", "Serper · بحث بالصورة",
            "SerpApi · Google Lens"} <= set(names)
    labels = {line["label"]: line["value"] for line in view["cost"]["lines"]}
    assert "Serper · 4 استعلامات" in labels and "SerpApi Lens · بحث واحد" in labels
    assert "$0.01" in labels["SerpApi Lens · بحث واحد"]                    # $0.015, to the cent as every line
    assert "SerpApi" in view["cost"]["note"] and "0.015" in view["cost"]["note"]
    assert "0.02" in view["cost"]["total"] and window["cost_usd"]["total"] == pytest.approx(0.023)
