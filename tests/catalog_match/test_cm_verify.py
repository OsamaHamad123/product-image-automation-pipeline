"""catalog_match.verify: one fail-closed Gemini client; the code decides MATCH (decision D6)."""

import base64
import io
import json
import socket

import numpy as np
import pytest
import requests
from PIL import Image

from catalog_match import verify as verify_mod
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, VlmImageVerdict
from catalog_match.verify import CircuitBreaker, GeminiVerifier, check_model_available, classify, make_verdict

MAPPINGS = {"al rawabi": {"brand": "Al Rawabi", "synonyms": ["الروابي"], "excluded_competitors": ["Almarai"]}}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket.socket, "connect", _refuse)


@pytest.fixture
def laban():
    return build_sku_spec({"name": "Al Rawabi Laban Up 180ml", "brand": "Al Rawabi"}, MAPPINGS)


def _fetched(w=600, h=800, seed=0):
    rng = np.random.default_rng(seed)
    img = Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return FetchedImage(candidate=Candidate(image_url=f"https://a.ae/{seed}.jpg"), ok=True,
                        content_sha256=str(seed), width=w, height=h, path_or_bytes=buf.getvalue())


class Resp:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self._text = text
        self.headers = headers or {}

    def json(self):
        if self._text is not None:
            return json.loads(self._text)
        return self._payload


def _reply(entries, best=1):
    text = json.dumps({"images": entries, "best_index": best})
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def _entry(i, **over):
    e = {"image_index": i, "brand_text": "Al Rawabi", "variant_text": "Laban Up", "size_text": "180 ml",
         "pack_count": 1, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
         "size_match": "yes"}
    e.update(over)
    return e


class Poster:
    """Replacement for requests.post that plays back a script and records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None, **_):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _verifier(breaker=None):
    return GeminiVerifier(api_key="test-key", model="gemini-test", breaker=breaker or CircuitBreaker(),
                          sleep=lambda _s: None)


FAIL_SCENARIOS = {
    # name: (responses in call order, expected HTTP calls, expected error code)
    "http_404": ([Resp(404, {"error": {"message": "model not found"}})], 1, "http_404"),
    "http_429_twice": ([Resp(429, {}), Resp(429, {})], 2, "http_429"),
    "http_500_twice": ([Resp(500, {}), Resp(500, {})], 2, "http_500"),
    "http_500_then_timeout": ([Resp(503, {}), requests.Timeout("read timed out")], 2, "timeout"),
    "timeout": ([requests.Timeout("read timed out")], 1, "timeout"),
    "connection_error": ([requests.ConnectionError("reset")], 1, "connection_error:ConnectionError"),
    "prose_not_json": ([Resp(200, {"candidates": [{"content": {"parts": [
        {"text": "Sure! Image 1 looks like Al Rawabi laban, it is valid."}]}}]})], 1, "parse_error"),
    "json_list": ([Resp(200, {"candidates": [{"content": {"parts": [{"text": "[1, 2, 3]"}]}}]})], 1,
                  "schema_error"),
    "valid_false_string": ([Resp(200, {"candidates": [{"content": {"parts": [
        {"text": "{\"valid\": \"false\"}"}]}}]})], 1, "schema_error"),
    "safety_block": ([Resp(200, {"promptFeedback": {"blockReason": "SAFETY"},
                                 "candidates": [{"content": {"parts": [{"text": "{}"}]}}]})], 1, "blocked:SAFETY"),
    "finish_safety": ([Resp(200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]})], 1,
                      "finish:SAFETY"),
    "body_not_json": ([Resp(200, text="<html>502 Bad Gateway</html>")], 1, "bad_json"),
    "empty_candidates": ([Resp(200, {"candidates": []})], 1, "no_candidates"),
    "images_not_dicts": ([_reply(["MATCH", "MATCH"])], 1, "schema_error"),
}


@pytest.mark.parametrize("name", sorted(FAIL_SCENARIOS))
def test_fail_closed(monkeypatch, laban, name):
    script, expected_http_calls, expected_error = FAIL_SCENARIOS[name]
    poster = Poster(script)
    monkeypatch.setattr(verify_mod.requests, "post", poster)
    breaker = CircuitBreaker()
    images = [_fetched(seed=1), _fetched(seed=2)]
    result = _verifier(breaker).verify(laban, images)
    assert result.status == "unknown", name
    assert result.error == expected_error
    assert [v.decision for v in result.verdicts] == ["UNKNOWN", "UNKNOWN"]
    assert [v.index for v in result.verdicts] == [0, 1]
    assert len(poster.calls) == expected_http_calls
    assert poster.script == []                  # no retries beyond the documented one
    assert breaker.consecutive_unknown == 1


def test_no_api_key_is_unknown_without_http(monkeypatch, laban):
    poster = Poster([])
    monkeypatch.setattr(verify_mod.requests, "post", poster)
    v = GeminiVerifier(api_key="", model="m", breaker=CircuitBreaker(), sleep=lambda _s: None)
    result = v.verify(laban, [_fetched()])
    assert result.status == "unknown" and result.error == "no_api_key"
    assert [x.decision for x in result.verdicts] == ["UNKNOWN"]
    assert poster.calls == []


def test_429_then_ok_is_retried_once(monkeypatch, laban):
    poster = Poster([Resp(429, {}, headers={"Retry-After": "1"}), _reply([_entry(1)])])
    monkeypatch.setattr(verify_mod.requests, "post", poster)
    slept = []
    v = GeminiVerifier(api_key="k", model="m", breaker=CircuitBreaker(), sleep=slept.append)
    result = v.verify(laban, [_fetched()])
    assert result.status == "ok"
    assert result.verdicts[0].decision == "MATCH"
    assert len(poster.calls) == 2 and slept == [1.0]


def test_code_decides(monkeypatch, laban):
    entries = [
        # The model is happy overall, but the printed size is 1 L for a 180 ml SKU.
        _entry(1, size_text="1L", overall="MATCH"),
        _entry(2, view="lifestyle"),
        _entry(3),
        _entry(4, variant_match="no"),
    ]
    monkeypatch.setattr(verify_mod.requests, "post", Poster([_reply(entries, best=1)]))
    images = [_fetched(seed=i) for i in range(4)]
    result = _verifier().verify(laban, images)
    assert result.status == "ok"
    decisions = [v.decision for v in result.verdicts]
    assert decisions[0] == "MISMATCH"
    assert decisions[1] != "MATCH" and decisions[1] == "UNSURE"
    assert decisions[2] == "MATCH"
    assert decisions[3] == "MISMATCH"
    assert result.verdicts[0].size_text == "1L"


@pytest.mark.parametrize("fields,expected", [
    # D6: nothing readable confirms the size, so the flags alone never make a MATCH
    ({"brand_match": "yes", "brand_text": "Al Rawabi", "view": "front_packshot", "variant_match": "unsure",
      "size_match": "unsure", "size_text": ""}, "UNSURE"),
    ({"brand_match": "unsure", "view": "front_packshot", "variant_match": "yes", "size_match": "yes"}, "UNSURE"),
    ({"brand_match": "no", "view": "front_packshot", "variant_match": "yes", "size_match": "yes"}, "MISMATCH"),
    ({"brand_match": "yes", "view": "banner", "variant_match": "yes", "size_match": "yes"}, "MISMATCH"),
    ({"brand_match": True, "view": "front_packshot", "variant_match": "yes", "size_match": "yes"}, "UNSURE"),
    ({"brand_match": "yes", "view": "front_packshot", "variant_match": "yes", "size_match": "yes",
      "size_text": "6 x 180 ml"}, "MISMATCH"),     # pack conflict read from the printed size
    ({"brand_match": "yes", "view": "front_packshot", "variant_match": "yes", "size_match": "yes",
      "size_text": "200 ml"}, "MISMATCH"),          # 180 vs 200 ml is beyond the 3 % tolerance
    ({"brand_match": "yes", "view": "front_packshot", "variant_match": "yes", "size_match": "yes",
      "size_text": "180 g"}, "UNSURE"),             # parsed, but not a volume
    ({"brand_match": "yes", "brand_text": "الروابي", "view": "front_packshot", "variant_match": "yes",
      "size_match": "yes", "size_text": "١٨٠ مل"}, "MATCH"),             # Arabic digits, unit and brand
])
def test_make_verdict_rules(laban, fields, expected):
    assert make_verdict(laban, 0, fields).decision == expected


def test_classify_ignores_model_decision_field(laban):
    v = VlmImageVerdict(index=0, brand_match="yes", view="front_packshot", variant_match="yes",
                        size_match="yes", size_text="1L", decision="MATCH")
    assert classify(laban, v) == "MISMATCH"


def test_header_key_and_resize(monkeypatch, laban):
    poster = Poster([_reply([_entry(i) for i in (1, 2, 3, 4)])])
    monkeypatch.setattr(verify_mod.requests, "post", poster)
    images = [_fetched(2000, 1500, 1), _fetched(1200, 3000, 2), _fetched(500, 400, 3), _fetched(1024, 1024, 4),
              _fetched(1600, 1600, 5)]            # a fifth image is never sent
    result = _verifier().verify(laban, images)
    assert result.status == "ok"
    [call] = poster.calls
    assert call["headers"]["x-goog-api-key"] == "test-key"
    assert "key=" not in call["url"]
    assert call["url"].endswith("/models/gemini-test:generateContent")
    assert call["timeout"] == 25
    parts = call["json"]["contents"][0]["parts"]
    labels = [p["text"] for p in parts[1:] if "text" in p]
    assert labels == ["Image 1", "Image 2", "Image 3", "Image 4"]
    sizes = []
    for p in parts:
        if "inlineData" in p:
            assert p["inlineData"]["mimeType"] == "image/jpeg"
            sizes.append(Image.open(io.BytesIO(base64.b64decode(p["inlineData"]["data"]))).size)
    assert len(sizes) == 4
    assert all(max(s) <= 1024 for s in sizes)
    assert sizes[0] == (1024, 768) and sizes[2] == (500, 400)     # downscaled, never upscaled
    gen = call["json"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    item_props = gen["responseSchema"]["properties"]["images"]["items"]["properties"]
    for field in ("brand_text", "variant_text", "size_text", "pack_count", "view",
                  "brand_match", "variant_match", "size_match"):
        assert field in item_props
    assert "best_index" in gen["responseSchema"]["properties"]
    prompt = parts[0]["text"]
    for needle in ("Al Rawabi", "الروابي", "Al Rawabi Laban Up 180ml", "laban", "180ml"):
        assert needle in prompt
    assert [v.decision for v in result.verdicts] == ["MATCH"] * 4 + ["UNKNOWN"]


def test_image_index_maps_back_to_input_positions(monkeypatch, laban):
    # The second image cannot be decoded, so only positions 0 and 2 are sent as Image 1 and 2.
    broken = FetchedImage(candidate=Candidate(image_url="https://a.ae/x.jpg"), ok=True, path_or_bytes=b"not an image")
    entries = [_entry(2, brand_match="no"), _entry(1)]           # answered out of order
    monkeypatch.setattr(verify_mod.requests, "post", Poster([_reply(entries)]))
    result = _verifier().verify(laban, [_fetched(seed=1), broken, _fetched(seed=3)])
    assert [(v.index, v.decision) for v in result.verdicts] == [(0, "MATCH"), (1, "UNKNOWN"), (2, "MISMATCH")]


def test_circuit_breaker(monkeypatch, laban):
    now = [1000.0]
    breaker = CircuitBreaker(threshold=5, cooldown_s=60, clock=lambda: now[0])
    poster = Poster([Resp(404, {})] * 5)
    monkeypatch.setattr(verify_mod.requests, "post", poster)
    v = GeminiVerifier(api_key="k", model="m", breaker=breaker, sleep=lambda _s: None)
    for _ in range(5):
        assert v.verify(laban, [_fetched()]).status == "unknown"
    assert breaker.is_open() and len(poster.calls) == 5

    # Open: the next verify makes no HTTP call and returns unknown.
    result = v.verify(laban, [_fetched()])
    assert result.status == "unknown" and result.error == "circuit_open"
    assert [x.decision for x in result.verdicts] == ["UNKNOWN"]
    assert len(poster.calls) == 5

    # After the cool-down one trial call goes through; an ok reply resets the breaker.
    now[0] += 61
    poster.script = [_reply([_entry(1)])]
    result = v.verify(laban, [_fetched()])
    assert result.status == "ok" and len(poster.calls) == 6
    assert not breaker.is_open() and breaker.consecutive_unknown == 0


def test_ok_resets_the_consecutive_count(monkeypatch, laban):
    breaker = CircuitBreaker(threshold=5)
    script = [Resp(500, {}), Resp(500, {})] * 4 + [_reply([_entry(1)])] + [Resp(404, {})] * 4
    monkeypatch.setattr(verify_mod.requests, "post", Poster(script))
    v = GeminiVerifier(api_key="k", model="m", breaker=breaker, sleep=lambda _s: None)
    statuses = [v.verify(laban, [_fetched()]).status for _ in range(9)]
    assert statuses == ["unknown"] * 4 + ["ok"] + ["unknown"] * 4
    assert not breaker.is_open() and breaker.consecutive_unknown == 4


def test_module_breaker_is_the_default():
    assert GeminiVerifier(api_key="k").breaker is verify_mod.BREAKER
    assert verify_mod.is_open() == verify_mod.BREAKER.is_open()


def test_check_model_available(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return Resp(404 if "retired" in url else 200, {})

    monkeypatch.setattr(verify_mod.requests, "get", fake_get)
    ok = check_model_available(api_key="k", model="models/gemini-test")
    assert ok and ok.status == "ok"
    gone = check_model_available(api_key="k", model="gemini-retired")
    assert not gone and gone.status == "model_not_found"
    assert calls[0][0].endswith("/models/gemini-test") and "key=" not in calls[0][0]
    assert calls[0][1] == {"x-goog-api-key": "k"}
    assert not check_model_available(api_key="", model="m")
    assert len(calls) == 2


# -- live run 2026-09-30: the model read the grade correctly, the code did not compare it ---------

@pytest.mark.parametrize("sheet_name,printed", [
    ("VIRGINIA L/MEAT TUNA WATER 170GM", "WHITE MEAT"),                        # row 58
    ("VIRGINIA WHITE TUNA S/F OIL 170GM", "LIGHT MEAT Solid in Sunflower Oil"),  # row 60
])
def test_tuna_meat_grade_on_the_label_is_compared(sheet_name, printed):
    from catalog_match.identity import build_sku_spec
    from catalog_match.verify import build_prompt, make_verdict

    spec = build_sku_spec({"name": sheet_name, "brand": "VIRGINIA"}, {})
    reading = {"brand_text": "VIRGINIA", "variant_text": printed, "size_text": "170g", "view": "front_packshot",
               "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
    assert make_verdict(spec, 0, reading).decision == "MISMATCH"
    assert "tuna_meat" in build_prompt(spec, 1), "the model is told which grade to check"


def test_printed_piece_count_of_a_pieces_sku_is_not_a_pack_mismatch():
    # live row 14: 'ASHOKA PLAIN PARATHA 5S 400GM' is one 400 g pack of 5 parathas.
    from catalog_match.identity import build_sku_spec
    from catalog_match.verify import build_prompt, make_verdict

    spec = build_sku_spec({"name": "ASHOKA PLAIN PARATHA 5S 400GM", "brand": "ASHOKA"}, {})
    reading = {"brand_text": "Ashoka", "variant_text": "Plain Paratha", "size_text": "400 g", "pack_count": 5,
               "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
    assert make_verdict(spec, 0, reading).decision == "MATCH"
    assert make_verdict(spec, 0, dict(reading, pack_count=1)).decision == "MATCH"
    assert make_verdict(spec, 0, dict(reading, pack_count=3)).decision == "UNSURE"
    assert make_verdict(spec, 0, dict(reading, size_text="2 kg")).decision == "MISMATCH"
    assert "5 pieces" in build_prompt(spec, 1)
