"""catalog_match.verifiers: model-agnostic label readers, the Claude adapter and the strong-model cascade.

Everything runs offline: the Anthropic SDK client and Gemini's HTTP are test doubles, the spend counter is
in memory (MariaDB only in the store's own round-trip test). The code-side decision (verify.make_verdict /
classify) is the production one in every test.
"""

import base64
import io
import json
import logging
import socket
from types import SimpleNamespace

import anthropic
import httpx2
import numpy as np
import pytest
from PIL import Image

from catalog_match import pipeline, settings
from catalog_match import verify as verify_mod
from catalog_match.facade import outcome_summary
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult, VlmImageVerdict
from catalog_match.verifiers import cascade as cascade_mod
from catalog_match.verifiers import claude as claude_mod
from catalog_match.verifiers import pricing, registry
from catalog_match.verifiers.cascade import CascadeVerifier, merge_verdict, needs_rejudge, needs_second_look
from catalog_match.verifiers.claude import ClaudeVerifier
from catalog_match.verifiers.spend import MemorySpendStore
from catalog_match.verify import CircuitBreaker, GeminiVerifier, make_verdict

MAPPINGS = {"al rawabi": {"brand": "Al Rawabi", "synonyms": ["الروابي"], "excluded_competitors": ["Almarai"]}}
KEY = "sk-ant-TEST-0123456789-SECRET"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(settings, "_config", None)
    for name in ("VERIFIER_PRIMARY", "VERIFIER_STRONG", "VERIFIER_MONTHLY_BUDGET_USD", "VERIFIER_STRONG_MAX_CALLS",
                 "VERIFIER_REJUDGE_MAX_CALLS", "MODEL_PRICES", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(claude_mod, "_CLIENTS", {})


@pytest.fixture
def laban():
    return build_sku_spec({"name": "Al Rawabi Laban Up 180ml", "brand": "Al Rawabi"}, MAPPINGS)


def _fetched(seed=0, w=600, h=800):
    rng = np.random.default_rng(seed)
    img = Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return FetchedImage(candidate=Candidate(image_url=f"https://a.ae/{seed}.jpg", title="Al Rawabi Laban Up 180ml"),
                        ok=True, content_sha256=str(seed), width=w, height=h, path_or_bytes=buf.getvalue())


def _entry(i, **over):
    e = {"image_index": i, "brand_text": "Al Rawabi", "variant_text": "Laban Up", "size_text": "180 ml",
         "pack_count": 1, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
         "size_match": "yes"}
    e.update(over)
    return e


def _packshot(seed=1, size=(800, 800)) -> bytes:
    """A white-background product shot that passes the quality gate (the pipeline tests' drawing)."""
    from PIL import ImageDraw
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([int(w * 0.28), int(h * 0.12), int(w * 0.72), int(h * 0.88)],
                fill=((seed * 67) % 200, (seed * 131) % 200, (seed * 29) % 200))
    d.rectangle([int(w * 0.33), int(h * 0.4), int(w * 0.67), int(h * 0.5)], fill=(250, 250, 250))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


UNSURE_SIZE = {"size_text": "", "size_match": "unsure"}          # the size was not readable
MISMATCH_BRAND = {"brand_text": "Almarai", "brand_match": "no"}


# ---------------------------------------------------------------------------
# Registry and prices
# ---------------------------------------------------------------------------

def test_model_ids():
    assert registry.parse_model_id("gemini:gemini-3.5-flash").id == "gemini:gemini-3.5-flash"
    assert registry.parse_model_id(" Claude:claude-sonnet-5-5 ").id == "claude:claude-sonnet-5-5"
    assert registry.parse_model_id("anthropic:claude-haiku-4-5").id == "claude:claude-haiku-4-5"
    assert registry.parse_model_id("gemini:models/gemini-3.1-flash-lite").model == "gemini-3.1-flash-lite"
    for bad in ("", "off", "gpt:gpt-5", "claude:", "claude:bad model", "gemini-3.5-flash", None, 7):
        assert registry.parse_model_id(bad) is None, bad
    assert registry.is_off("off") and registry.is_off(" OFF ") and not registry.is_off("gemini:x")
    assert registry.parse_model_id("claude:claude-fable-5-1").supported is False


def test_prices_default_override_and_bad_entries(caplog):
    assert pricing.load_prices("")["claude:claude-opus-5-5"] == {"input": 4.0, "output": 20.0}
    text = json.dumps({"claude:claude-opus-5-5": {"input": 3, "output": 15}, "gemini:gemini-3.5-flash": {"input": -1,
                       "output": 2}, "nonsense": {"input": 1, "output": 1}, "claude:claude-new-9": {"input": 7, "output": 9}})
    prices = pricing.load_prices(text)
    assert prices["claude:claude-opus-5-5"] == {"input": 3.0, "output": 15.0}
    assert prices["gemini:gemini-3.5-flash"] == pricing.DEFAULT_PRICES["gemini:gemini-3.5-flash"]   # negative ignored
    assert prices["claude:claude-new-9"] == {"input": 7.0, "output": 9.0}
    with caplog.at_level(logging.WARNING):
        assert pricing.load_prices("{not json") == pricing.DEFAULT_PRICES
    # a model nobody priced is charged high on purpose (a budget is never overrun by a missing price)
    unknown = registry.parse_model_id("claude:claude-fable-5-1")
    assert pricing.price_of(unknown, pricing.DEFAULT_PRICES) == pricing.UNKNOWN_PRICES["claude"]
    assert pricing.cost_usd(registry.parse_model_id("claude:claude-sonnet-5-5"), 1_000_000, 100_000,
                            pricing.DEFAULT_PRICES) == 3.0


def test_cost_per_100_products_is_small_for_the_defaults():
    flash_lite = registry.parse_model_id("gemini:gemini-3.1-flash-lite")
    strong = registry.parse_model_id("gemini:gemini-3.5-flash")
    # primary reading + a strong look for every product stays under one cent per product
    assert pricing.estimate_per_100(flash_lite, "primary") + pricing.estimate_per_100(strong, "strong") < 1.0
    opus = registry.parse_model_id("claude:claude-opus-5-5")
    assert pricing.estimate_per_100(opus, "strong") > pricing.estimate_per_100(strong, "strong")


# ---------------------------------------------------------------------------
# Gemini path: unchanged contract, plus usage
# ---------------------------------------------------------------------------

class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class Poster:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None, **_):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.script.pop(0)


def _gemini_reply(entries, usage=None):
    payload = {"candidates": [{"content": {"parts": [{"text": json.dumps({"images": entries, "best_index": 1})}]},
                               "finishReason": "STOP"}]}
    if usage is not None:
        payload["usageMetadata"] = usage
    return Resp(200, payload)


def test_gemini_reports_usage_and_keeps_its_contract(monkeypatch, laban):
    poster = Poster([_gemini_reply([_entry(1)], {"promptTokenCount": 1500, "candidatesTokenCount": 200,
                                                   "thoughtsTokenCount": 50}),
                     _gemini_reply([_entry(1)]), Resp(500, {}), Resp(500, {})])
    monkeypatch.setattr(verify_mod.requests, "post", poster)
    v = GeminiVerifier(api_key="g-key", model="gemini-test", breaker=CircuitBreaker(), sleep=lambda _s: None)
    result = v.verify(laban, [_fetched(1)])
    assert result.status == "ok" and result.verdicts[0].decision == "MATCH" and result.calls == 1
    assert result.usage == [{"provider": "gemini", "model": "gemini-test", "images": 1, "input_tokens": 1500,
                             "output_tokens": 250, "estimated": False}]
    estimated = v.verify(laban, [_fetched(2)]).usage[0]
    assert estimated["estimated"] is True and estimated["input_tokens"] > 1000
    failed = v.verify(laban, [_fetched(3)])
    assert failed.status == "unknown" and failed.usage == []          # no answer, nothing billed
    assert "g-key" not in json.dumps([result.usage, estimated])
    # the focused second look: same schema, one image, a larger size, FOCUS_LINES in the prompt
    prompt = poster.calls[0]["json"]["contents"][0]["parts"][0]["text"]
    assert "FOCUSED SECOND LOOK" not in prompt
    assert verify_mod.build_prompt(laban, 1) == verify_mod.build_prompt(laban, 1, focus=False)
    assert "FOCUSED SECOND LOOK" in verify_mod.build_prompt(laban, 1, focus=True)


# ---------------------------------------------------------------------------
# Claude adapter (mocked SDK client)
# ---------------------------------------------------------------------------

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, code, headers=None):
    return cls("error", response=httpx2.Response(code, request=REQ, headers=headers or {}), body=None)


def message(entries=None, stop="end_turn", text=None, usage=(1800, 320), model="claude-sonnet-5-5"):
    body = text if text is not None else json.dumps({"images": entries or [], "best_index": 1})
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=body)]
    use = None if usage is None else SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1],
                                                     cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(stop_reason=stop, content=content if stop != "refusal" else [], usage=use, model=model)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """client.messages and client.beta.messages share one script (thinking models use the beta entry point)."""

    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)


def _claude(script, model="claude-sonnet-5-5", breaker=None, **kw):
    client = FakeClient(script)
    v = ClaudeVerifier(model, api_key=KEY, client=client, breaker=breaker or CircuitBreaker(),
                       sleep=lambda _s: None, **kw)
    return v, client


def test_claude_structured_output_and_image_encoding(laban):
    v, client = _claude([message([_entry(1), _entry(2, **MISMATCH_BRAND)])])
    result = v.verify(laban, [_fetched(1, 2000, 1500), _fetched(2)])
    assert result.status == "ok" and result.calls == 1
    assert [d.decision for d in result.verdicts] == ["MATCH", "MISMATCH"]
    call = client.messages.calls[0]
    # thinking models: effort low, the server-side refusal fallback through the beta entry point
    assert call["model"] == "claude-sonnet-5-5" and call["output_config"]["effort"] == "low"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    schema = call["output_config"]["format"]
    assert schema["type"] == "json_schema" and schema["schema"]["required"] == ["images", "best_index"]
    item = schema["schema"]["properties"]["images"]["items"]
    assert set(item["required"]) == set(verify_mod._IMAGE_FIELDS) and item["additionalProperties"] is False
    assert "temperature" not in call and "thinking" not in call
    content = call["messages"][0]["content"]
    assert [c["type"] for c in content] == ["text", "image", "text", "image", "text"]
    assert content[0]["text"] == "Image 1" and content[2]["text"] == "Image 2"
    assert content[-1]["text"] == verify_mod.build_prompt(laban, 2)
    src = content[1]["source"]
    assert src["type"] == "base64" and src["media_type"] == "image/jpeg"
    img = Image.open(io.BytesIO(base64.b64decode(src["data"])))
    assert img.format == "JPEG" and max(img.size) == verify_mod.LONG_SIDE        # downscaled, never upscaled
    assert result.usage == [{"provider": "claude", "model": "claude-sonnet-5-5", "images": 2, "input_tokens": 1800,
                             "output_tokens": 320, "estimated": False}]


def test_claude_haiku_plain_request(laban):
    v, client = _claude([message([_entry(1)], model="claude-haiku-4-5")], model="claude-haiku-4-5")
    assert v.verify(laban, [_fetched(1)]).status == "ok"
    call = client.messages.calls[0]
    assert "betas" not in call and "fallbacks" not in call and "effort" not in call["output_config"]


@pytest.mark.parametrize("reply, error", [
    (message(stop="refusal"), "refusal"),
    (message([_entry(1)], stop="max_tokens"), "max_tokens"),
    (message(text="Sure! It is Al Rawabi."), "parse_error"),
    (message([_entry(0)]), "schema_error:image_index"),
    (message(text="[1, 2]"), "schema_error"),
])
def test_claude_fails_closed_on_bad_answers(laban, reply, error):
    breaker = CircuitBreaker()
    v, client = _claude([reply], breaker=breaker)
    result = v.verify(laban, [_fetched(1)])
    assert result.status == "unknown" and result.error == error
    assert [d.decision for d in result.verdicts] == ["UNKNOWN"]
    assert breaker.consecutive_unknown == 1 and len(client.messages.calls) == 1


def test_claude_key_rejected_is_a_notice_without_retry(laban, caplog):
    v, client = _claude([status_error(anthropic.AuthenticationError, 401)])
    with caplog.at_level(logging.DEBUG):
        result = v.verify(laban, [_fetched(1)])
    assert (result.status, result.error, result.notices) == ("unknown", "http_401", ["claude_key_rejected"])
    assert len(client.messages.calls) == 1
    v, client = _claude([status_error(anthropic.PermissionDeniedError, 403)])
    assert v.verify(laban, [_fetched(1)]).notices == ["claude_key_rejected"]
    assert KEY not in caplog.text and KEY not in json.dumps(result.usage)


def test_claude_retries_429_and_connection_errors_once(laban):
    slept = []
    v, client = _claude([status_error(anthropic.RateLimitError, 429, {"retry-after": "3"}), message([_entry(1)])])
    v.sleep = slept.append
    result = v.verify(laban, [_fetched(1)])
    assert result.status == "ok" and len(client.messages.calls) == 2 and slept == [3.0]
    v, client = _claude([status_error(anthropic.RateLimitError, 429), status_error(anthropic.InternalServerError, 500)])
    result = v.verify(laban, [_fetched(1)])
    assert (result.status, result.error, len(client.messages.calls)) == ("unknown", "http_500", 2)
    v, client = _claude([anthropic.APIConnectionError(request=REQ), anthropic.APITimeoutError(request=REQ)])
    result = v.verify(laban, [_fetched(1)])
    assert (result.status, result.error, len(client.messages.calls)) == ("unknown", "timeout", 2)
    v, client = _claude([status_error(anthropic.NotFoundError, 404)])
    assert v.verify(laban, [_fetched(1)]).notices == ["claude_model_not_found"]


def test_claude_counts_a_timed_out_request_it_sent_again(laban):
    # the timed-out request was most likely billed: it counts, with estimated tokens (was calls 1, one usage entry)
    v, client = _claude([anthropic.APITimeoutError(request=REQ), message([_entry(1)])])
    result = v.verify(laban, [_fetched(1)])
    assert result.status == "ok" and len(client.messages.calls) == 2 and result.calls == 2
    assert [(u["estimated"], u.get("timed_out", False)) for u in result.usage] == [(True, True), (False, False)]
    v, client = _claude([anthropic.APITimeoutError(request=REQ), anthropic.APITimeoutError(request=REQ)])
    result = v.verify(laban, [_fetched(1)])
    assert (result.status, result.calls) == ("unknown", 2) and [u["timed_out"] for u in result.usage] == [True, True]


def test_claude_without_key_makes_no_call(laban, monkeypatch):
    monkeypatch.setattr(claude_mod, "_client_for", lambda *a: pytest.fail("no client without a key"))
    v = ClaudeVerifier("claude-sonnet-5-5", breaker=CircuitBreaker())
    result = v.verify(laban, [_fetched(1)])
    assert (result.status, result.error, result.notices) == ("unknown", "no_api_key", ["claude_key_missing"])


def test_claude_key_comes_from_settings_with_a_fixed_base_url(monkeypatch, laban):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.invalid")
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.messages = FakeMessages([message([_entry(1)])])
            self.beta = SimpleNamespace(messages=self.messages)

    monkeypatch.setattr(anthropic, "Anthropic", Recorder)
    result = ClaudeVerifier("claude-sonnet-5-5", breaker=CircuitBreaker()).verify(laban, [_fetched(1)])
    assert result.status == "ok"
    assert seen["api_key"] == KEY and seen["base_url"] == "https://api.anthropic.com" and seen["max_retries"] == 0


def test_claude_request_through_the_real_sdk(laban):
    """The real SDK client over a mock transport: the request it builds from our arguments is valid on the wire."""
    seen = []

    def handler(request):
        seen.append(request)
        body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "text", "text": json.dumps({"images": [_entry(1)], "best_index": 1})}],
                "usage": {"input_tokens": 2100, "output_tokens": 410}}
        return httpx2.Response(200, json=body)

    client = anthropic.Anthropic(api_key=KEY, base_url="https://api.anthropic.com", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    v = ClaudeVerifier("claude-opus-5-5", api_key=KEY, client=client, breaker=CircuitBreaker())
    result = v.verify(laban, [_fetched(1)])
    assert result.status == "ok" and result.verdicts[0].decision == "MATCH"
    assert result.usage[0]["input_tokens"] == 2100 and result.usage[0]["output_tokens"] == 410
    request = seen[0]
    sent = json.loads(request.content)
    assert request.headers["x-api-key"] == KEY and "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert sent["fallbacks"] == "default" and sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["type"] == "json_schema" and KEY not in request.content.decode()


def test_claude_breaker_skips_calls_while_open(laban):
    breaker = CircuitBreaker(threshold=2)
    v, client = _claude([status_error(anthropic.InternalServerError, 500)] * 4, breaker=breaker)
    v.verify(laban, [_fetched(1)])
    v.verify(laban, [_fetched(1)])
    result = v.verify(laban, [_fetched(1)])
    assert result.error == "circuit_open" and len(client.messages.calls) == 4


# ---------------------------------------------------------------------------
# Cascade rules
# ---------------------------------------------------------------------------

class Reader:
    """A reader double: per call, the readings for each image (production make_verdict decides)."""

    def __init__(self, provider, model, script, long_side=1024):
        self.provider, self.model, self.long_side = provider, model, long_side
        self.script = list(script)
        self.calls = []

    def verify(self, spec, images):
        self.calls.append([f.candidate.image_url for f in images])
        item = self.script.pop(0)
        if isinstance(item, VerificationResult):
            return item
        verdicts = [make_verdict(spec, i, reading) for i, reading in enumerate(item)]
        usage = [{"provider": self.provider, "model": self.model, "images": len(images), "input_tokens": 4000,
                  "output_tokens": 500, "estimated": False}]
        return VerificationResult(status="ok", verdicts=verdicts, calls=1, usage=usage)


def _cascade(primary_script, strong_script, tiers=None, spent=0.0, budget=5.0, store=None, **kw):
    primary = Reader("gemini", "gemini-3.1-flash-lite", primary_script)
    strong = Reader("claude", "claude-sonnet-5-5", strong_script, long_side=1568) if strong_script is not None else None
    store = store if store is not None else MemorySpendStore({"strong": spent})
    tiers = tiers or {}
    c = CascadeVerifier(primary, strong, primary_ref=registry.parse_model_id("gemini:gemini-3.1-flash-lite"),
                        strong_ref=registry.parse_model_id("claude:claude-sonnet-5-5"), budget_usd=budget,
                        spend_store=store, tier_of=lambda spec, f: tiers.get(f.candidate.image_url, 1), **kw)
    return c, primary, strong, store


def _images(n=3):
    return [_fetched(i) for i in range(1, n + 1)]


def test_unsure_becomes_match_with_one_strong_look(laban):
    images = _images(3)
    c, primary, strong, store = _cascade([[_entry(1, **MISMATCH_BRAND), _entry(2, **UNSURE_SIZE), _entry(3, **UNSURE_SIZE)]],
                                         [[_entry(1)]])
    result = c.verify(laban, images)
    assert result.status == "ok" and result.calls == 2
    assert [v.decision for v in result.verdicts] == ["MISMATCH", "MATCH", "UNSURE"]
    assert result.verdicts[1].index == 1 and result.verdicts[1].size_text == "180 ml"
    assert strong.calls == [[images[1].candidate.image_url]]           # the top-ranked UNSURE image only
    roles = [(u["role"], u["provider"], u["model"]) for u in result.usage]
    assert roles == [("primary", "gemini", "gemini-3.1-flash-lite"), ("strong", "claude", "claude-sonnet-5-5")]
    strong_use = result.usage[1]
    assert strong_use["image_index"] == 1 and strong_use["primary_decision"] == "UNSURE"
    assert strong_use["strong_decision"] == "MATCH" and strong_use["applied"] is True
    assert strong_use["usd"] == pricing.cost_usd(registry.parse_model_id("claude:claude-sonnet-5-5"), 4000, 500,
                                                 pricing.DEFAULT_PRICES)
    assert [e["role"] for e in store.added] == ["primary", "strong"]
    assert store.role_spend("strong") == pytest.approx(strong_use["usd"])


def test_strong_can_reject_an_unsure_image(laban):
    c, *_ = _cascade([[_entry(1, **UNSURE_SIZE)]], [[_entry(1, size_text="1 L", size_match="no")]])
    assert c.verify(laban, _images(1)).verdicts[0].decision == "MISMATCH"


def test_a_primary_mismatch_is_never_turned_into_a_match(laban):
    primary = make_verdict(laban, 0, _entry(1, **MISMATCH_BRAND))
    strong = make_verdict(laban, 0, _entry(1))
    assert merge_verdict(primary, strong) is primary
    assert merge_verdict(primary, None) is primary
    unsure = make_verdict(laban, 2, _entry(1, **UNSURE_SIZE))
    assert merge_verdict(unsure, VlmImageVerdict(index=0, decision="UNKNOWN")) is unsure
    assert merge_verdict(unsure, strong).decision == "MATCH" and merge_verdict(unsure, strong).index == 2
    assert not needs_second_look(laban, primary) and needs_second_look(laban, unsure)
    # a batch of MISMATCHes gets no second look at all
    c, _, strong_reader, _ = _cascade([[_entry(1, **MISMATCH_BRAND), _entry(2, view="banner")]], [[_entry(1)]])
    result = c.verify(laban, _images(2))
    assert [v.decision for v in result.verdicts] == ["MISMATCH", "MISMATCH"] and strong_reader.calls == []


def test_no_strong_look_when_the_batch_has_its_match_or_only_tier3(laban):
    c, _, strong, _ = _cascade([[_entry(1, **UNSURE_SIZE), _entry(2)]], [[_entry(1)]])
    assert c.verify(laban, _images(2)).calls == 1 and strong.calls == []
    images = _images(2)
    tiers = {images[0].candidate.image_url: 3, images[1].candidate.image_url: 3}
    c, _, strong, _ = _cascade([[_entry(1, **UNSURE_SIZE), _entry(2, **UNSURE_SIZE)]], [[_entry(1)]], tiers=tiers)
    assert c.verify(laban, images).calls == 1 and strong.calls == []


def test_one_strong_look_per_product(laban):
    images = _images(2)
    c, _, strong, _ = _cascade([[_entry(1, **UNSURE_SIZE)], [_entry(1, **UNSURE_SIZE)], [_entry(1, **UNSURE_SIZE)]],
                               [[_entry(1, **UNSURE_SIZE)], [_entry(1)]])
    c.verify(laban, images[:1])
    c.verify(laban, images[1:])                     # the pipeline's second batch for the same SKU
    assert len(strong.calls) == 1
    other = build_sku_spec({"name": "Al Rawabi Laban Up 180ml", "brand": "Al Rawabi"}, MAPPINGS)
    c.verify(other, images[:1])                     # another product gets its own look
    assert len(strong.calls) == 2


def test_budget_stop_and_unknown_spend(laban):
    c, _, strong, _ = _cascade([[_entry(1, **UNSURE_SIZE)]], [[_entry(1)]], spent=4.999)
    result = c.verify(laban, _images(1))
    assert strong.calls == [] and result.notices == ["strong_budget_exhausted"]
    assert result.verdicts[0].decision == "UNSURE" and result.calls == 1
    c, _, strong, _ = _cascade([[_entry(1, **UNSURE_SIZE)]], [[_entry(1)]], budget=0)
    # a budget of 0 is the owner's «no second look», not a used-up month: no "budget ran out" alert follows
    assert c.verify(laban, _images(1)).notices == ["strong_budget_zero"] and strong.calls == []
    c, _, strong, _ = _cascade([[_entry(1, **UNSURE_SIZE)]], [[_entry(1)]], store=MemorySpendStore(fail_reads=True))
    assert c.verify(laban, _images(1)).notices == ["strong_budget_unknown"] and strong.calls == []


def test_off_switch_and_primary_failure(laban):
    c, primary, _, store = _cascade([[_entry(1, **UNSURE_SIZE)]], None)
    result = c.verify(laban, _images(1))
    assert result.calls == 1 and result.verdicts[0].decision == "UNSURE" and [e["role"] for e in store.added] == ["primary"]
    down = VerificationResult(status="unknown", calls=1, error="http_503",
                              verdicts=[VlmImageVerdict(index=0, decision="UNKNOWN")])
    c, _, strong, _ = _cascade([down], [[_entry(1)]])
    result = c.verify(laban, _images(1))
    assert result is down and strong.calls == []      # VERIFIER_DOWN keeps its meaning; nothing is accepted


def test_a_failed_strong_look_keeps_the_primary_verdict(laban):
    failed = VerificationResult(status="unknown", calls=1, error="http_500",
                                verdicts=[VlmImageVerdict(index=0, decision="UNKNOWN")])
    c, *_ = _cascade([[_entry(1, **UNSURE_SIZE)]], [failed])
    result = c.verify(laban, _images(1))
    assert result.status == "ok" and result.verdicts[0].decision == "UNSURE" and result.calls == 2
    assert result.notices == ["strong_failed"]
    keyless = VerificationResult(status="unknown", calls=0, error="no_api_key", notices=["claude_key_missing"],
                                 verdicts=[VlmImageVerdict(index=0, decision="UNKNOWN")])
    c, *_ = _cascade([[_entry(1, **UNSURE_SIZE)]], [keyless])
    assert c.verify(laban, _images(1)).notices == ["strong:claude_key_missing"]     # the strong call's own notice
    # a strong result that is not ok is never applied, even when it carries a MATCH reading
    broken = VerificationResult(status="unknown", calls=1, error="parse_error",
                                verdicts=[make_verdict(laban, 0, _entry(1))],
                                usage=[{"provider": "claude", "model": "claude-sonnet-5-5", "images": 1,
                                        "input_tokens": 1500, "output_tokens": 200, "estimated": False}])
    assert broken.verdicts[0].decision == "MATCH"
    c, *_ = _cascade([[_entry(1, **UNSURE_SIZE)]], [broken])
    result = c.verify(laban, _images(1))
    assert result.verdicts[0].decision == "UNSURE" and result.usage[-1]["applied"] is False


def test_reader_notices_say_which_role_they_come_from(laban):
    """A key problem of the strong look is tagged 'strong:', the primary reader's stays as it is: the health page
    tells the owner to fix the strong model's key only when that key is the problem."""
    rejected = VerificationResult(status="unknown", calls=1, error="http_401", notices=["gemini_key_rejected"],
                                  verdicts=[VlmImageVerdict(index=0, decision="UNKNOWN")])
    c, _, strong, _ = _cascade([rejected], [[_entry(1)]])
    result = c.verify(laban, _images(1))
    assert result.notices == ["gemini_key_rejected"] and strong.calls == []
    rejected = VerificationResult(status="unknown", calls=1, error="http_401", notices=["gemini_key_rejected"],
                                  verdicts=[VlmImageVerdict(index=0, decision="UNKNOWN")])
    c, *_ = _cascade([[_entry(1, **UNSURE_SIZE)]], [rejected])
    result = c.verify(laban, _images(1))
    assert result.notices == ["strong:gemini_key_rejected"] and result.verdicts[0].decision == "UNSURE"


def test_a_raising_reader_fails_closed(laban):
    class Boom:
        provider, model = "claude", "claude-sonnet-5-5"

        def verify(self, spec, images):
            raise RuntimeError("boom")

    c = CascadeVerifier(Boom(), None)
    result = c.verify(laban, _images(2))
    assert result.status == "unknown" and [v.decision for v in result.verdicts] == ["UNKNOWN", "UNKNOWN"]


# ---------------------------------------------------------------------------
# Defaults from settings, and the pipeline
# ---------------------------------------------------------------------------

def test_default_verifier_follows_settings(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    v = cascade_mod.default_verifier()
    assert isinstance(v.primary, GeminiVerifier) and v.primary.model == "gemini-3.5-flash" and not v.primary.focus
    assert isinstance(v.strong, GeminiVerifier) and v.strong.model == "gemini-3.5-flash" and v.strong.focus
    assert v.strong.max_images == 1 and v.strong.long_side == pricing.STRONG_LONG_SIDE
    assert (v.max_strong_calls, v.budget_usd) == (1, 5.0)
    monkeypatch.setenv("VERIFIER_PRIMARY", "claude:claude-haiku-4-5")
    monkeypatch.setenv("VERIFIER_STRONG", "off")
    monkeypatch.setenv("VERIFIER_MONTHLY_BUDGET_USD", "12.5")
    v = cascade_mod.default_verifier()
    assert isinstance(v.primary, ClaudeVerifier) and v.primary.model == "claude-haiku-4-5" and v.strong is None
    assert v.budget_usd == 12.5
    monkeypatch.setenv("VERIFIER_PRIMARY", "gpt:nope")
    monkeypatch.setenv("VERIFIER_STRONG", "claude:claude-opus-5-5")
    monkeypatch.setenv("VERIFIER_STRONG_MAX_CALLS", "0")
    v = cascade_mod.default_verifier()
    assert v.primary.model == "gemini-3.5-flash" and v.strong is None        # a bad id falls back, 0 looks = off


def test_nothing_configured_is_inert(monkeypatch, laban):
    """No keys: the default makes no HTTP call, no SDK client and no database write."""
    monkeypatch.setattr(verify_mod.requests, "post", lambda *a, **k: pytest.fail("no Gemini call without a key"))
    monkeypatch.setattr(claude_mod, "_client_for", lambda *a: pytest.fail("no Claude client without a key"))
    store = MemorySpendStore()
    monkeypatch.setattr(cascade_mod, "MariaDbSpendStore", lambda: store)
    monkeypatch.setattr(verify_mod, "BREAKER", CircuitBreaker())
    v = cascade_mod.default_verifier()
    v.primary.breaker = CircuitBreaker()
    result = v.verify(laban, _images(2))
    assert result.status == "unknown" and result.error == "no_api_key" and store.added == []


def test_an_injected_verifier_bypasses_the_default(monkeypatch, laban):
    monkeypatch.setattr(cascade_mod, "default_verifier", lambda: pytest.fail("the default must not be built"))
    img = _fetched(5)
    c = Candidate(image_url=img.candidate.image_url, page_url="https://www.luluhypermarket.com/en-ae/al-rawabi-laban-up-180ml/p/1",
                  title="Al Rawabi Laban Up 180ml", page_title="Al Rawabi Laban Up 180ml", provider="serper")

    class Provider:
        name, sanctioned, kind, fallback = "serper", True, "search", False

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper", status="ok", http_status=200, candidates=[c])

    class Fetcher:
        def fetch(self, cands, spec):
            return [FetchedImage(candidate=x, ok=True, width=800, height=800, path_or_bytes=_packshot(),
                                 content_sha256="x") for x in cands]

    class Injected:
        def verify(self, spec, images):
            return VerificationResult(status="ok", calls=1, verdicts=[make_verdict(spec, i, _entry(1))
                                                                      for i in range(len(images))])

    outcome = pipeline.find_product_image(laban, providers=[Provider()], fetcher=Fetcher(), verifier=Injected())
    assert outcome.vlm_calls == 1 and outcome.vlm_usage == [] and outcome.verifier_notices == []


def test_usage_and_notices_reach_the_outcome_trace(monkeypatch, laban):
    img = _fetched(6)
    c = Candidate(image_url=img.candidate.image_url, page_url="https://www.luluhypermarket.com/en-ae/al-rawabi-laban-up-180ml/p/1",
                  title="Al Rawabi Laban Up 180ml", page_title="Al Rawabi Laban Up 180ml", provider="serper")

    class Provider:
        name, sanctioned, kind, fallback = "serper", True, "search", False

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper", status="ok", http_status=200, candidates=[c])

    class Fetcher:
        def fetch(self, cands, spec):
            return [FetchedImage(candidate=x, ok=True, width=800, height=800, path_or_bytes=_packshot(),
                                 content_sha256="x") for x in cands]

    built, *_ = _cascade([[_entry(1, **UNSURE_SIZE)]], [[_entry(1)]])
    monkeypatch.setattr(cascade_mod, "default_verifier", lambda: built)
    outcome = pipeline.find_product_image(laban, providers=[Provider()], fetcher=Fetcher())
    assert outcome.vlm_calls == 2
    assert [u["role"] for u in outcome.vlm_usage] == ["primary", "strong"]
    assert outcome.ranked[0].verdict.decision == "MATCH"            # the strong reading replaced the UNSURE one
    summary = outcome_summary(outcome)
    assert [u["model"] for u in summary["vlm_usage"]] == ["gemini-3.1-flash-lite", "claude-sonnet-5-5"]
    assert summary["verifier_notices"] == []
    json.dumps(summary)                              # trace-safe


# ---------------------------------------------------------------------------
# The strong re-judge of a cheap MISMATCH that rests only on a variant / size 'no' (VERIFIER_REJUDGE_MAX_CALLS)
# Run exports 2026-10-04/05: 'Buy Zwan Chicken Luncheon Meat 340 g' (talabat, tier 1) was read 'ZWAN' / 'LUNCHEON',
# variant 'no', and rejected for good.
# ---------------------------------------------------------------------------

@pytest.fixture
def zwan():
    return build_sku_spec({"name": "ZWAN CHICKEN LUNCHEON MEAT 340GM", "brand": "ZWAN"}, {})


def _zwan(i, **over):
    """The cheap reading of the talabat listing: brand yes, variant 'LUNCHEON' read as 'no', size unreadable."""
    e = {"image_index": i, "brand_text": "ZWAN", "variant_text": "LUNCHEON", "size_text": "", "pack_count": 1,
         "view": "front_packshot", "brand_match": "yes", "variant_match": "no", "size_match": "unsure"}
    e.update(over)
    return e


ZWAN_FULL = {"variant_text": "Chicken Luncheon Meat", "size_text": "340 g", "variant_match": "yes", "size_match": "yes"}


def test_needs_rejudge_only_for_a_variant_or_size_no(zwan, laban):
    assert needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1)))
    assert needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1, size_text="200 g", size_match="no", variant_match="yes")))
    assert needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1, size_text="200 g", size_match="no")))
    assert not needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1, brand_match="no")))            # brand 'no' is final
    assert not needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1, view="banner")))               # the picture, not a flag
    assert not needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1, **ZWAN_FULL)))                 # a MATCH
    assert not needs_rejudge(zwan, make_verdict(zwan, 0, _zwan(1, variant_match="unsure")))      # UNSURE: a second look
    assert not needs_rejudge(zwan, None)
    # a competitor printed on the pack refutes the brand like a brand 'no', whatever its flag says
    competitor = make_verdict(laban, 0, _entry(1, brand_text="Almarai", brand_match="unsure", variant_match="no"))
    assert competitor.decision == "MISMATCH" and not needs_rejudge(laban, competitor)


def test_merge_verdict_lets_only_a_rejudge_replace_a_mismatch(zwan):
    cheap = make_verdict(zwan, 2, _zwan(1))
    strong = make_verdict(zwan, 0, _zwan(1, **ZWAN_FULL))
    assert merge_verdict(cheap, strong) is cheap
    merged = merge_verdict(cheap, strong, index=2, rejudge=True)
    assert merged.decision == "MATCH" and merged.index == 2
    assert merge_verdict(cheap, None, rejudge=True) is cheap                                     # a failed call
    assert merge_verdict(cheap, VlmImageVerdict(index=0, decision="UNKNOWN"), rejudge=True) is cheap


def test_rejudge_replaces_the_cheap_mismatch_with_the_strong_reading(zwan):
    images = _images(2)
    c, primary, strong, store = _cascade([[_zwan(1), _zwan(2, brand_text="Bordon", brand_match="no")]],
                                         [[_zwan(1, **ZWAN_FULL)]], max_rejudges=1)
    result = c.verify(zwan, images)
    assert result.status == "ok" and result.calls == 2
    assert [v.decision for v in result.verdicts] == ["MATCH", "MISMATCH"]
    assert result.verdicts[0].variant_text == "Chicken Luncheon Meat" and result.verdicts[0].index == 0
    assert strong.calls == [[images[0].candidate.image_url]]
    use = result.usage[-1]
    assert use["role"] == "strong" and use["rejudge"] is True and use["applied"] is True
    assert use["primary_decision"] == "MISMATCH" and use["strong_decision"] == "MATCH"
    assert store.role_spend("strong") == pytest.approx(use["usd"])                    # the month budget counts it


def test_rejudge_gives_only_what_the_strong_reader_gives(zwan):
    """The strong reading may confirm the MISMATCH (a printed variant the SKU does not name) or leave it UNSURE."""
    tandoori = _zwan(1, variant_text="Chicken Luncheon Meat Tandoori", size_text="340 g", size_match="yes")
    c, *_ = _cascade([[_zwan(1)]], [[tandoori]], max_rejudges=1)
    assert c.verify(zwan, _images(1)).verdicts[0].decision == "MISMATCH"
    beef = _zwan(1, variant_text="Beef Luncheon Meat", size_text="340 g", variant_match="yes", size_match="yes")
    c, *_ = _cascade([[_zwan(1)]], [[beef]], max_rejudges=1)
    assert c.verify(zwan, _images(1)).verdicts[0].decision == "MISMATCH"                 # the code still decides
    c, *_ = _cascade([[_zwan(1)]], [[_zwan(1, variant_text="Chicken Luncheon Meat", variant_match="yes")]],
                     max_rejudges=1)
    assert c.verify(zwan, _images(1)).verdicts[0].decision == "UNSURE"                   # the size still unread
    failed = VerificationResult(status="unknown", calls=1, error="http_500",
                                verdicts=[VlmImageVerdict(index=0, decision="UNKNOWN")])
    c, *_ = _cascade([[_zwan(1)]], [failed], max_rejudges=1)
    result = c.verify(zwan, _images(1))
    assert result.verdicts[0].decision == "MISMATCH" and result.notices == ["strong_failed"]
    assert result.calls == 2


def test_no_rejudge_for_a_brand_no_a_tier2_listing_or_a_batch_with_its_match(zwan):
    c, _, strong, _ = _cascade([[_zwan(1, brand_match="no", brand_text="Bordon")]], [[_zwan(1, **ZWAN_FULL)]],
                               max_rejudges=1)
    assert c.verify(zwan, _images(1)).verdicts[0].decision == "MISMATCH" and strong.calls == []
    images = _images(1)
    c, _, strong, _ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]], max_rejudges=1,
                               tiers={images[0].candidate.image_url: 2})
    assert c.verify(zwan, images).verdicts[0].decision == "MISMATCH" and strong.calls == []
    c, _, strong, _ = _cascade([[_zwan(1), _zwan(2, **ZWAN_FULL)]], [[_zwan(1, **ZWAN_FULL)]], max_rejudges=1)
    assert [v.decision for v in c.verify(zwan, _images(2)).verdicts] == ["MISMATCH", "MATCH"] and strong.calls == []


def test_rejudge_is_off_by_setting_and_never_more_than_one_per_product(zwan):
    c, _, strong, _ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]])                  # max_rejudges=0: off
    assert c.verify(zwan, _images(1)).verdicts[0].decision == "MISMATCH" and strong.calls == []
    images = _images(2)
    c, _, strong, _ = _cascade([[_zwan(1)], [_zwan(1)]], [[_zwan(1, variant_match="no")], [_zwan(1, **ZWAN_FULL)]],
                               max_rejudges=5, max_strong_calls=4)
    assert c.max_rejudges == 1                                                             # capped at 1
    c.verify(zwan, images[:1])
    second = c.verify(zwan, images[1:])                     # the pipeline's second batch for the same SKU
    assert len(strong.calls) == 1 and second.verdicts[0].decision == "MISMATCH"


def test_rejudge_has_its_own_allowance_after_the_second_look(zwan):
    """VERIFIER_STRONG_MAX_CALLS counts the second looks, the re-judge has its own one per product: the batch's
    second look goes first, the re-judge follows only while the batch has no MATCH; a strong model that is off
    (0 strong calls) makes neither."""
    images = _images(2)
    unread_size = {"variant_text": "Chicken Luncheon Meat", "variant_match": "yes"}
    batch = [[_zwan(1), _zwan(2, **unread_size)]]
    c, _, strong, store = _cascade(batch, [[_zwan(1, **unread_size)], [_zwan(1, **ZWAN_FULL)]], max_rejudges=1)
    result = c.verify(zwan, images)
    assert strong.calls == [[images[1].candidate.image_url], [images[0].candidate.image_url]]
    assert [v.decision for v in result.verdicts] == ["MATCH", "UNSURE"] and result.calls == 3
    assert [u.get("rejudge", False) for u in result.usage] == [False, False, True]
    assert [e["role"] for e in store.added] == ["primary", "strong", "strong"]
    # the second look found the MATCH: nothing left to re-judge
    c, _, strong, _ = _cascade(batch, [[_zwan(1, **ZWAN_FULL)], [_zwan(1, **ZWAN_FULL)]], max_rejudges=1)
    assert [v.decision for v in c.verify(zwan, images).verdicts] == ["MISMATCH", "MATCH"]
    assert strong.calls == [[images[1].candidate.image_url]]
    # the first batch's re-judge leaves the second batch its second look
    c, _, strong, _ = _cascade([[_zwan(1)], [_zwan(1, **unread_size)]],
                               [[_zwan(1, variant_match="no")], [_zwan(1, **ZWAN_FULL)]], max_rejudges=1)
    c.verify(zwan, images[:1])
    assert c.verify(zwan, images[1:]).verdicts[0].decision == "MATCH" and len(strong.calls) == 2
    c, _, strong, _ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]], max_rejudges=1, max_strong_calls=0)
    assert c.verify(zwan, _images(1)).verdicts[0].decision == "MISMATCH" and strong.calls == []


def test_rejudge_respects_the_month_budget(zwan):
    c, _, strong, _ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]], spent=4.999, max_rejudges=1)
    result = c.verify(zwan, _images(1))
    assert strong.calls == [] and result.notices == ["strong_budget_exhausted"]
    assert result.verdicts[0].decision == "MISMATCH" and result.calls == 1
    c, _, strong, _ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]], budget=0, max_rejudges=1)
    assert c.verify(zwan, _images(1)).notices == ["strong_budget_zero"] and strong.calls == []
    c, _, strong, _ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]], store=MemorySpendStore(fail_reads=True),
                               max_rejudges=1)
    assert c.verify(zwan, _images(1)).notices == ["strong_budget_unknown"] and strong.calls == []


def test_default_verifier_rejudges_once_unless_turned_off(monkeypatch):
    assert settings.verifier_rejudge_max_calls() == 1
    assert cascade_mod.default_verifier().max_rejudges == 1
    monkeypatch.setenv("VERIFIER_REJUDGE_MAX_CALLS", "3")
    assert settings.verifier_rejudge_max_calls() == 1 and cascade_mod.default_verifier().max_rejudges == 1
    monkeypatch.setenv("VERIFIER_REJUDGE_MAX_CALLS", "0")
    assert cascade_mod.default_verifier().max_rejudges == 0
    monkeypatch.setenv("VERIFIER_REJUDGE_MAX_CALLS", "lots")
    assert settings.verifier_rejudge_max_calls() == 1                    # unreadable: the default


def test_rejudged_listing_becomes_the_pick(monkeypatch, zwan):
    """End to end: the talabat listing the cheap reader rejected is pre-checked once the strong model re-reads it;
    with the re-judge off the row has no pick (the live run's outcome)."""
    img = _fetched(7)
    c = Candidate(image_url=img.candidate.image_url,
                  page_url="https://www.talabat.com/uae/grocery/600123/zwan-chicken-luncheon-meat-340-g",
                  title="Buy Zwan Chicken Luncheon Meat 340 g", page_title="Buy Zwan Chicken Luncheon Meat 340 g",
                  provider="serper", domain="talabat.com")

    class Provider:
        name, sanctioned, kind, fallback = "serper", True, "search", False

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper", status="ok", http_status=200, candidates=[c])

    class Fetcher:
        def fetch(self, cands, spec):
            return [FetchedImage(candidate=x, ok=True, width=800, height=800, path_or_bytes=_packshot(),
                                 content_sha256="z") for x in cands]

    outcomes = {}
    for label, rejudges in (("off", 0), ("on", 1)):
        built, *_ = _cascade([[_zwan(1)]], [[_zwan(1, **ZWAN_FULL)]], max_rejudges=rejudges)
        monkeypatch.setattr(cascade_mod, "default_verifier", lambda built=built: built)
        outcomes[label] = pipeline.find_product_image(zwan, providers=[Provider()], fetcher=Fetcher())
    assert outcomes["off"].decision == "REVIEW_UNSELECTED" and outcomes["off"].vlm_calls == 1
    on = outcomes["on"]
    assert on.ranked[0].score.tier == 1
    assert on.decision in ("REVIEW_PRESELECTED", "AUTO_PUBLISH") and on.winner.candidate.image_url == c.image_url
    assert on.winner.verdict.decision == "MATCH" and on.vlm_calls == 2
    assert [u.get("rejudge", False) for u in on.vlm_usage] == [False, True]
