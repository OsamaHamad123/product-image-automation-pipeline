"""Settings · «نماذج التحقق» and the new keys (verifier package).

* The PHP list of models, prices and cost estimates is the Python one (registry / pricing), checked by running both.
* modelsData(): what the tab shows from system_settings / .env / defaults, and the missing-key warnings.
* Through the Laravel kernel: the tab renders, saving accepts only supported models and sane numbers, a Gemini
  primary also updates gemini_model, and no stored key (Anthropic, SerpApi included) ever reaches a page.
"""

import json
import re

import pytest

from catalog_match.verifiers import pricing, registry
from test_laqta_health import NEEDS_PHP, SECRETS, _kernel, _php, _put, _settings, app_env, php_value  # noqa: F401


@NEEDS_PHP
def test_php_models_prices_and_estimates_are_the_python_ones():
    out = _php("$out['models'] = SettingsController::VERIFIER_MODELS; $out['estimate'] = SettingsController::VERIFIER_ESTIMATE;"
               " foreach (array_keys(SettingsController::VERIFIER_MODELS) as $id) {"
               " $p = SettingsController::verifierPrices('');"
               " $out['per100'][$id] = [SettingsController::estimatePer100($id, 'primary', $p),"
               " SettingsController::estimatePer100($id, 'strong', $p)]; }")
    assert {k: v["label"] for k, v in out["models"].items()} == registry.SUPPORTED_MODELS
    assert {k: {"input": v["input"], "output": v["output"]} for k, v in out["models"].items()} == pricing.DEFAULT_PRICES
    est = out["estimate"]
    assert (est["primary_images"], est["primary_long_side"], est["strong_long_side"], est["prompt_tokens"],
            est["gemini_image_tokens"], est["output_per_image"]) == (
        pricing.PRIMARY_IMAGES, pricing.PRIMARY_LONG_SIDE, pricing.STRONG_LONG_SIDE, pricing.PROMPT_TOKENS,
        pricing.GEMINI_IMAGE_TOKENS, pricing.OUTPUT_PER_IMAGE)
    assert est["output_base"] == pricing.OUTPUT_BASE
    for model_id, (primary, strong) in out["per100"].items():
        ref = registry.parse_model_id(model_id)
        assert primary == pytest.approx(pricing.estimate_per_100(ref, "primary")), model_id
        assert strong == pytest.approx(pricing.estimate_per_100(ref, "strong")), model_id


def _stored(**values):
    return {k: {"value": v, "updated_at": 1} for k, v in values.items()}


@NEEDS_PHP
def test_models_tab_data():
    cases = [
        _stored(),
        _stored(verifier_primary="claude:claude-sonnet-5-5", verifier_strong="off", gemini_api_key="g",
                verifier_monthly_budget_usd="12.5",
                model_prices=json.dumps({"claude:claude-sonnet-5-5": {"input": 3, "output": 15},
                                         "gemini:gemini-3.5-flash": {"input": "abc", "output": 1}})),
        _stored(gemini_model="gemini-3.5-flash", verifier_strong="claude:claude-opus-9", anthropic_api_key="a",
                gemini_api_key="g"),
    ]
    spend = {"month": "2026-10", "strong_usd": 13.0, "total_usd": 14.0}
    out = _php("foreach (" + php_value(cases) + " as $i => $s) {"
               " $out[] = SettingsController::modelsData($s, $i === 1 ? " + php_value(spend) + " : null); }")
    default, claude, custom = out
    assert (default["primary"], default["strong"], default["budget"]) == ("gemini:gemini-3.1-flash-lite",
                                                                           "gemini:gemini-3.5-flash", "5")
    assert default["spend"] is None and len(default["warnings"]) == 2           # no Gemini key for either model
    assert all("Gemini" in w for w in default["warnings"])
    assert claude["strong_off"] and claude["budget"] == "12.5"
    assert claude["warnings"] == [w for w in claude["warnings"] if "Anthropic" in w] and len(claude["warnings"]) == 1
    rows = {r["id"]: r for r in claude["rows"]}
    assert (rows["claude:claude-sonnet-5-5"]["input"], rows["claude:claude-sonnet-5-5"]["output"]) == ("3", "15")
    assert (rows["gemini:gemini-3.5-flash"]["input"], rows["gemini:gemini-3.5-flash"]["output"]) == ("0.5", "3")
    assert rows["claude:claude-sonnet-5-5"]["key_saved"] is False and rows["gemini:gemini-3.5-flash"]["key_saved"]
    assert claude["spend"]["exhausted"] is True and claude["spend"]["strong"] == "$13.00"
    assert custom["primary"] == "gemini:gemini-3.5-flash"                        # follows gemini_model
    assert custom["strong_supported"] is False and custom["warnings"] == []
    for data in out:
        assert "sk-" not in json.dumps(data) and '"g"' not in json.dumps(data["rows"])


@NEEDS_PHP
def test_a_zero_budget_is_shown_as_zero():
    """«0 يعني بلا نظرة تانية»: a saved 0 is what the worker uses (config.py keeps any non-empty value), so the tab must
    show 0, not the $5 default, or the next save of the tab silently turns the second look back on."""
    spend = {"month": "2026-10", "strong_usd": 0.0, "total_usd": 0.2}
    out = _php("$out[] = SettingsController::modelsData(" + php_value(_stored(verifier_monthly_budget_usd="0")) + ", "
               + php_value(spend) + ");")
    assert out[0]["budget"] == "0" and out[0]["budget_text"] == "$0.00"
    assert out[0]["spend"]["left"] == "$0.00"
    # and it is not «this month's budget ran out, the strong model waits for next month»: 0 means no second look
    assert out[0]["spend"]["exhausted"] is False


def test_models_tab_renders_and_saves_only_supported_choices(app_env):
    db = app_env["db"]
    _put(db, {"verifier_primary": "", "verifier_strong": "", "verifier_monthly_budget_usd": "", "model_prices": ""})
    prices_in = {"gemini__gemini-3_1-flash-lite": "0.3", "gemini__gemini-3_5-flash": "0.5",
                 "claude__claude-haiku-4-5": "1", "claude__claude-sonnet-5-5": "2", "claude__claude-opus-5-5": "4"}
    prices_out = {k: "10" for k in prices_in}
    env = app_env["env"]
    out = _kernel(env, [
        ["GET", "/settings?tab=models", {}],
        ["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.5-flash",
                               "verifier_strong": "claude:claude-sonnet-5-5", "verifier_monthly_budget_usd": "7.5",
                               "price_input": prices_in, "price_output": prices_out}],
    ])
    pages = list(out)
    page = out[0]["body"]
    assert out[0]["status"] == 200 and 'id="lq-settings-models-title"' in page
    assert re.search(r'href="[^"]+\?tab=models"[^>]*aria-current="page"', page)
    assert 'name="verifier_primary"' in page and 'name="verifier_strong"' in page and 'value="off"' in page
    assert "إيقاف النموذج القوي" in page and 'name="verifier_monthly_budget_usd"' in page
    assert 'name="price_input[claude__claude-opus-5-5]"' in page
    for leftover in ("{{", "{!!", "<x-lq", "@include"):
        assert leftover not in page

    assert out[1]["location"].endswith("?tab=models#lq-settings-models") and not out[1]["flash"]["warnings"]
    after = _settings(db)
    assert after["verifier_primary"] == "gemini:gemini-3.5-flash" and after["gemini_model"] == "gemini-3.5-flash"
    assert after["verifier_strong"] == "claude:claude-sonnet-5-5" and after["verifier_monthly_budget_usd"] == "7.5"
    prices = json.loads(after["model_prices"])
    assert prices["gemini:gemini-3.1-flash-lite"] == {"input": 0.3, "output": 10}
    assert pricing.load_prices(after["model_prices"])["claude:claude-opus-5-5"] == {"input": 4.0, "output": 10.0}

    # unsupported models, a negative budget and a non-number price: nothing changes, each refusal is said
    out = _kernel(env, [["POST", "/settings", {
        "section": "models", "verifier_primary": "gpt:gpt-9", "verifier_strong": "claude:x",
        "verifier_monthly_budget_usd": "-1", "price_input": dict(prices_in, **{"claude__claude-opus-5-5": "abc"}),
        "price_output": prices_out}]])
    pages += out
    assert len(out[0]["flash"]["warnings"]) == 4 and _settings(db) == after

    out = _kernel(env, [
        ["POST", "/settings", {"section": "models", "verifier_primary": "claude:claude-haiku-4-5",
                               "verifier_strong": "off", "verifier_monthly_budget_usd": "0"}],
        ["POST", "/settings", {"section": "anthropic", "anthropic_api_key": "  sk-ant-NEW-KEY-123456  "}],
        ["GET", "/settings?tab=models", {}],
        ["GET", "/settings?tab=keys", {}],
    ])
    pages += out
    assert out[0]["flash"]["success"] and not out[0]["flash"]["warnings"]
    after = _settings(db)
    assert after["verifier_primary"] == "claude:claude-haiku-4-5" and after["gemini_model"] == "gemini-3.5-flash"
    assert after["verifier_strong"] == "off" and after["verifier_monthly_budget_usd"] == "0"
    assert json.loads(after["model_prices"]) == prices                          # no price fields: unchanged
    assert after["anthropic_api_key"] == "sk-ant-NEW-KEY-123456"

    models_page, keys_page = out[2]["body"], out[3]["body"]
    assert re.search(r'<option value="off"[^>]*selected', models_page)
    assert re.search(r'<option value="claude:claude-haiku-4-5"[^>]*selected', models_page)
    assert re.search(r'name="verifier_monthly_budget_usd"[^>]*value="0"', models_page)   # the saved 0, not $5
    assert "Anthropic (Claude)" in keys_page and "SerpApi" in keys_page
    for response in pages:
        body = json.dumps(response, ensure_ascii=False)
        assert "sk-ant-NEW-KEY-123456" not in body and "NEW-KEY-123456"[-6:] not in response["body"]
        for value in SECRETS.values():
            assert value not in response["body"]


def test_bad_save_leaves_every_value(app_env):
    db = app_env["db"]
    _put(db, {"verifier_primary": "gemini:gemini-3.1-flash-lite", "verifier_strong": "gemini:gemini-3.5-flash",
              "verifier_monthly_budget_usd": "5", "model_prices": ""})
    before = _settings(db)
    out = _kernel(app_env["env"], [["POST", "/settings", {
        "section": "models", "verifier_primary": "claude:claude-fable-5-1", "verifier_strong": ["off"],
        "verifier_monthly_budget_usd": "5000", "price_input": "x", "price_output": {"a": "1"}}]])
    after = _settings(db)
    for key in ("verifier_primary", "verifier_strong", "verifier_monthly_budget_usd", "model_prices", "gemini_model"):
        assert after.get(key) == before.get(key), key
    assert len(out[0]["flash"]["warnings"]) == 4
