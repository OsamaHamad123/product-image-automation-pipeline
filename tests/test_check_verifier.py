"""main.check_verifier: the worker's start-up check follows the primary label reader chosen in Settings."""
import main
from catalog_match import settings as cm_settings
from catalog_match import verify as cm_verify


def _settings(monkeypatch, **values):
    real_get = cm_settings.get
    monkeypatch.setattr(cm_settings, "get", lambda name: values[name] if name in values else real_get(name))


def test_claude_primary_without_an_anthropic_key_says_so(monkeypatch):
    _settings(monkeypatch, VERIFIER_PRIMARY="claude:claude-haiku-4-5", ANTHROPIC_API_KEY="", GEMINI_API_KEY="g-key")
    notice = main.check_verifier()
    assert notice.startswith("VERIFIER_NOT_CONFIGURED_CLAUDE:")
    assert "Gemini" not in notice


def test_claude_primary_with_a_key_needs_no_gemini_key(monkeypatch):
    _settings(monkeypatch, VERIFIER_PRIMARY="claude:claude-haiku-4-5", ANTHROPIC_API_KEY="a-key", GEMINI_API_KEY="")
    monkeypatch.setattr(cm_verify, "check_model_available",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Gemini check for a Claude primary")))
    assert main.check_verifier() == ""


def test_gemini_primary_checks_the_chosen_model(monkeypatch):
    _settings(monkeypatch, VERIFIER_PRIMARY="gemini:gemini-3.5-flash", GEMINI_API_KEY="g-key")
    seen = {}

    def check(api_key=None, model=None, timeout=10.0):
        seen["model"] = model
        return cm_verify.ModelCheck(True, "ok", model)

    monkeypatch.setattr(cm_verify, "check_model_available", check)
    assert main.check_verifier() == ""
    assert seen["model"] == "gemini-3.5-flash"


def test_gemini_primary_without_a_key_keeps_the_gemini_notice(monkeypatch):
    _settings(monkeypatch, VERIFIER_PRIMARY="", GEMINI_API_KEY="")
    assert main.check_verifier().startswith("VERIFIER_NOT_CONFIGURED: no Gemini key")
