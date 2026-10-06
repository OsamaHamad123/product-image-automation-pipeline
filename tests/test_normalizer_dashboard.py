"""Settings · «نماذج التحقق»: the switch of the reading of abbreviated sheet names (QUERY_NORMALIZER).

* modelsData(): 'gemini' by default (the setting's default), the saved value, an unknown value read as 'off' (as
  catalog_match.settings.query_normalizer reads it), the missing Gemini key, and the cost per 100 products, the same
  as the Python estimate (normalizer.estimate_usd).
* Through the Laravel kernel: the tab shows the switch in Arabic, saving accepts only 'gemini' / 'off', a form
  without the field leaves it alone, and config.py reads what the page saved.
"""

import re

import pytest

from catalog_match import normalizer, settings
from catalog_match.verifiers import pricing
from test_laqta_health import NEEDS_PHP, _kernel, _php, _put, _settings, _sql, app_env, php_value  # noqa: F401


def _stored(**values):
    return {k: {"value": v, "updated_at": 1} for k, v in values.items()}


@NEEDS_PHP
def test_the_estimate_and_the_modes_are_the_python_ones(monkeypatch):
    monkeypatch.delenv("QUERY_NORMALIZER", raising=False)
    out = _php("$out['estimate'] = SettingsController::NORMALIZER_ESTIMATE;"
               " $out['modes'] = SettingsController::QUERY_NORMALIZER_MODES;"
               " $out['default'] = SettingsController::QUERY_NORMALIZER_DEFAULT;"
               " $out['per100'] = SettingsController::normalizerPer100(SettingsController::verifierPrices(''));")
    tin, tout = normalizer.estimate_tokens(normalizer.build_prompt(
        "AMERICAN GOLD LGT MEAT TUNA FLAKE IN SUNFLOWER OIL 160GM", "AMERICAN GOLD"))
    assert (out["estimate"]["input_tokens"], out["estimate"]["output_tokens"]) == (tin, tout)
    assert out["estimate"]["model"] == f"{normalizer.PROVIDER}:{normalizer.MODEL}" in pricing.DEFAULT_PRICES
    assert out["modes"] == list(settings.QUERY_NORMALIZER_MODES)
    assert out["default"] == settings.DEFAULTS["QUERY_NORMALIZER"] == "gemini"
    assert out["per100"] == pytest.approx(round(100 * normalizer.estimate_usd(), 3))


@NEEDS_PHP
def test_models_tab_data_for_the_switch(monkeypatch):
    monkeypatch.delenv("QUERY_NORMALIZER", raising=False)
    cases = [_stored(), _stored(query_normalizer="off", gemini_api_key="g"), _stored(query_normalizer="claude"),
             _stored(query_normalizer="GEMINI", gemini_api_key="g")]
    out = _php("foreach (" + php_value(cases) + " as $s) { $out[] = SettingsController::modelsData($s, null); }")
    default, off, unknown, on = out
    assert (default["normalizer"], default["normalizer_on"], default["normalizer_key_saved"]) == ("gemini", True, False)
    assert default["normalizer_per100_text"].startswith("$0.0")
    assert len(default["warnings"]) == 2                                   # the switch adds no top warning
    assert (off["normalizer"], off["normalizer_on"]) == ("off", False)
    assert (unknown["normalizer"], unknown["normalizer_on"]) == ("off", False)  # never a surprise paid call
    assert (on["normalizer"], on["normalizer_on"], on["normalizer_key_saved"]) == ("gemini", True, True)


def test_the_switch_renders_and_saves_only_gemini_or_off(app_env, monkeypatch):
    db = app_env["db"]
    env = dict(app_env["env"])
    env.pop("QUERY_NORMALIZER", None)
    before = _settings(db).get("query_normalizer")
    try:
        _sql(db, "DELETE FROM system_settings WHERE `key` = 'query_normalizer'")
        out = _kernel(env, [
            ["GET", "/settings?tab=models", {}],
            ["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.1-flash-lite",
                                   "verifier_strong": "off", "verifier_monthly_budget_usd": "5",
                                   "query_normalizer": "off"}],
            ["GET", "/settings?tab=models", {}],
        ])
        page = out[0]["body"]
        assert 'name="query_normalizer"' in page and "قراءة أسماء الشيت المختصرة" in page
        assert re.search(r'<option value="gemini"[^>]*selected', page)        # on by default
        assert "لكل 100 منتج" in page and "موقّفة" in page
        for leftover in ("{{", "{!!", "<x-lq", "@include"):
            assert leftover not in page
        assert out[1]["flash"]["success"] and not out[1]["flash"]["warnings"]
        assert _settings(db)["query_normalizer"] == "off"
        assert re.search(r'<option value="off"[^>]*selected', out[2]["body"].split('name="query_normalizer"')[1])

        # a value the page never offers: unchanged, and said; a form without the field leaves it alone
        out = _kernel(env, [
            ["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.1-flash-lite",
                                   "verifier_strong": "off", "verifier_monthly_budget_usd": "5",
                                   "query_normalizer": "claude:claude-opus-5-5"}],
            ["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.1-flash-lite",
                                   "verifier_strong": "off", "verifier_monthly_budget_usd": "5",
                                   "query_normalizer": ["gemini"]}],
            ["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.1-flash-lite",
                                   "verifier_strong": "off", "verifier_monthly_budget_usd": "5"}],
        ])
        assert out[0]["flash"]["warnings"] == ["اختيار قراءة الأسماء المختصرة مش مفهوم؛ ما تغيّر."]
        assert len(out[1]["flash"]["warnings"]) == 1 and not out[2]["flash"]["warnings"]
        assert _settings(db)["query_normalizer"] == "off"

        # the worker reads what the page saved (config.load_db_config -> catalog_match.settings)
        import config
        for name, value in list(vars(config).items()):        # load_db_config rewrites them: restored after the test
            if name.isupper():
                monkeypatch.setattr(config, name, value)
        _kernel(env, [["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.1-flash-lite",
                                              "verifier_strong": "off", "verifier_monthly_budget_usd": "5",
                                              "query_normalizer": "gemini"}]])
        config.load_db_config()
        monkeypatch.setattr(settings, "_config", config)
        assert config.QUERY_NORMALIZER == "gemini" and settings.query_normalizer() == "gemini"
    finally:
        _sql(db, "DELETE FROM system_settings WHERE `key` = 'query_normalizer'")
        if before is not None:
            _put(db, {"query_normalizer": before})
