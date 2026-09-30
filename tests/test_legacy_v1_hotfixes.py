"""v1 rollback path hotfixes (WP-4): no unverified pick counts as success, Gemini fails closed.

Ports of the skeptic replays skep_vl/replay_gemini_reject.py and replay_fallback.py.
Everything runs offline: search, download and Gemini are replaced, sockets are blocked.
"""

import asyncio
import io
import socket

import numpy as np
import pytest
from PIL import Image, ImageDraw

import config
import image_search


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _packshot_bytes(color=(30, 90, 200), size=800, seed=0):
    """A labelled bottle on pure #FFFFFF (the look v1's gate used to call 'overexposed')."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (size, size), (255, 255, 255))
    pw, ph = int(size * 0.4), int(size * 0.75)
    x0, y0 = (size - pw) // 2, (size - ph) // 2
    body = np.ones((ph, pw, 3), np.float32) * np.array(color, np.float32)
    body += rng.normal(0, 6, body.shape)
    img.paste(Image.fromarray(np.clip(body, 0, 255).astype(np.uint8)), (x0, y0))
    d = ImageDraw.Draw(img)
    d.rectangle([x0 + pw * 0.1, y0 + ph * 0.3, x0 + pw * 0.9, y0 + ph * 0.6], fill=(245, 240, 230))
    for k in range(6):
        d.text((x0 + pw * 0.15, y0 + ph * 0.32 + k * 18), "ALMARAI LABAN 1L", fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


R_TOP = {"url": "https://a.ae/almarai-laban-full-fat-1l.jpg", "title": "Almarai Laban Full Fat 1L",
         "width": 800, "height": 800}
R_SECOND = {"url": "https://c.ae/almarai-laban.jpg", "title": "Almarai Laban", "width": 800, "height": 800}
BLOBS = {R_TOP["url"]: _packshot_bytes((30, 90, 200), seed=1), R_SECOND["url"]: _packshot_bytes((30, 150, 60), seed=2)}


@pytest.fixture(autouse=True)
def offline_v1(monkeypatch):
    """Legacy engine, no network, no local models, no DB-backed side effects."""
    def _refuse(*_a, **_k):
        raise OSError("network access blocked in an offline test")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setenv("SEARCH_ENGINE", "v1")
    monkeypatch.setattr(config, "SEARCH_ENGINE", "v1", raising=False)
    monkeypatch.setattr(config, "DISABLE_LOCAL_AI_MODELS", True)
    monkeypatch.setattr(config, "MIN_IMAGE_WIDTH", 100)
    monkeypatch.setattr(config, "MIN_IMAGE_HEIGHT", 100)
    monkeypatch.setattr(config, "USE_MOONDREAM_CHECK", False)
    monkeypatch.setattr(config, "FILTER_COMPETITORS", True)


def install_downloads(monkeypatch, failures=()):
    async def fake_batch(urls):
        return {u: ((None, "HTTP_403") if u in failures else (BLOBS[u], "VERIFICATION_SUCCESS")) for u in urls}
    monkeypatch.setattr(image_search, "execute_batch_processing", fake_batch)


def install_gemini(monkeypatch, verdict_by_url):
    """Replace the Gemini vision check; the verdict is chosen by the image that was written to disk."""
    by_bytes = {BLOBS[u]: v for u, v in verdict_by_url.items()}
    calls = []

    def fake(path, name, brand):
        with open(path, "rb") as fh:
            data = fh.read()
        calls.append(data)
        return by_bytes[data]

    monkeypatch.setattr(image_search, "validate_image_via_gemini_vision", fake)
    return calls


def status_of(trace, url):
    return [c["status"] for step in trace["steps"] for c in step["candidates"] if c["url"] == url]


# ---------------------------------------------------------------------------
# evaluate_and_choose_best_image: the last resort (hotfix a)
# ---------------------------------------------------------------------------

def test_gemini_rejected_images_are_never_returned(monkeypatch):
    """replay_gemini_reject: two gate-passing packshots that Gemini rejects -> no pick at all."""
    install_downloads(monkeypatch)
    calls = install_gemini(monkeypatch, {R_TOP["url"]: False, R_SECOND["url"]: False})
    trace = {}
    item, score = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP), dict(R_SECOND)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True,
        trace=trace, step_name="t", query="q")
    assert len(calls) == 2            # both passed the (fixed) quality gate and reached Gemini
    assert item is None and score == 0
    assert status_of(trace, R_TOP["url"]) == ["rejected"]
    assert status_of(trace, R_SECOND["url"]) == ["rejected"]


def test_gemini_unavailable_gives_flagged_unverified_fallback(monkeypatch):
    """The same two images when Gemini cannot answer -> returned only as an unverified review item."""
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: None, R_SECOND["url"]: None})
    trace = {}
    item, _ = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP), dict(R_SECOND)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True,
        trace=trace, step_name="t", query="q")
    assert item["url"] == R_TOP["url"]
    assert item["needs_review"] is True and item["unverified"] is True
    assert item["clip_score"] is None
    assert status_of(trace, R_TOP["url"]) == ["unverified_fallback"]
    assert "accepted" not in status_of(trace, R_SECOND["url"])


def test_rejected_top_is_skipped_by_the_fallback(monkeypatch):
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: False, R_SECOND["url"]: None})
    item, _ = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP), dict(R_SECOND)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True,
        trace={}, step_name="t", query="q")
    assert item["url"] == R_SECOND["url"] and item["unverified"] is True


def test_failed_download_is_never_returned(monkeypatch):
    """replay_fallback download_fail: the top candidate's download failed with HTTP 403."""
    install_downloads(monkeypatch, failures={R_TOP["url"]})
    install_gemini(monkeypatch, {R_SECOND["url"]: False})
    item, _ = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP), dict(R_SECOND)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True,
        trace={}, step_name="t", query="q")
    assert item is None

    install_gemini(monkeypatch, {R_SECOND["url"]: None})
    item, _ = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP), dict(R_SECOND)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True,
        trace={}, step_name="t", query="q")
    assert item["url"] == R_SECOND["url"] and item["needs_review"] is True


def test_verified_pick_is_not_flagged(monkeypatch):
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: None, R_SECOND["url"]: True})
    trace = {}
    item, _ = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP), dict(R_SECOND)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True,
        trace=trace, step_name="t", query="q")
    assert item["url"] == R_SECOND["url"]             # the Gemini-confirmed one, not the higher-ranked
    assert item["unverified"] is False and item["needs_review"] is False
    assert item["clip_score"] is None                 # no local model: no fabricated 1.0
    assert status_of(trace, R_SECOND["url"]) == ["accepted"]


def test_no_bktree_answer_substitution(monkeypatch):
    """A pHash hit on another SKU's image must not replace the answer (hotfix e)."""
    import image_dedup_bktree
    tree = image_dedup_bktree.PerceptualDeduplicationTree()
    phash = image_dedup_bktree.calculate_phash(Image.open(io.BytesIO(BLOBS[R_TOP["url"]])).convert("RGB"))
    tree.insert(phash, "other", {"cloudinary_url": "https://res.cloudinary.com/x/other-sku.jpg",
                                 "product_name": "Almarai Laban Low Fat 1L"})
    monkeypatch.setattr(image_search, "get_bktree", lambda: tree)
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: True, R_SECOND["url"]: True})
    item, _ = image_search.evaluate_and_choose_best_image(
        [dict(R_TOP)], "Laban Full Fat 1L", "Almarai", requires_brand_match=True, trace={}, step_name="t",
        query="q")
    assert item["url"] == R_TOP["url"]
    assert item.get("source") != "visual_duplicate"


# ---------------------------------------------------------------------------
# search_best_product_image (v1): loops never stop on an unverified pick (hotfix b)
# ---------------------------------------------------------------------------

def install_search(monkeypatch, results_by_query):
    ran = []

    def fake_search(q):
        ran.append(q)
        return [dict(r) for r in results_by_query.get(q, [])]

    monkeypatch.setattr(image_search, "run_parallel_consensus_search", fake_search)
    monkeypatch.setattr(image_search, "expand_query_via_gemini",
                        lambda name, brand: ["q1 almarai laban", "q2 almarai laban", "q3 almarai laban"])
    import google_sheets
    monkeypatch.setattr(google_sheets, "align_brand_via_gemini", lambda name, brand: brand)
    return ran


MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["Al Marai"], "excluded_competitors": []}}


def test_v1_continues_past_unverified_and_returns_first_unverified(monkeypatch):
    ran = install_search(monkeypatch, {"6281007031119": [R_TOP], "q1 almarai laban": [R_SECOND]})
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: None, R_SECOND["url"]: None})
    trace = {}
    result = image_search.search_best_product_image(
        "Almarai Laban Full Fat 1L", "Laban Full Fat 1L", "Almarai", barcode="6281007031119",
        brand_mappings=MAPPINGS, skip_cache=True, trace=trace)
    # The barcode round and every text query ran: an unverified hit does not stop the search.
    assert ran == ["6281007031119", "q1 almarai laban", "q2 almarai laban", "q3 almarai laban"]
    assert result["url"] == R_TOP["url"]               # the FIRST unverified result
    assert result["needs_review"] is True and result["unverified"] is True
    assert result["clip_score"] is None
    assert status_of(trace, R_TOP["url"]) == ["unverified_fallback"]


def test_v1_prefers_a_verified_result_found_later(monkeypatch):
    ran = install_search(monkeypatch, {"6281007031119": [R_TOP], "q2 almarai laban": [R_SECOND]})
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: None, R_SECOND["url"]: True})
    result = image_search.search_best_product_image(
        "Almarai Laban Full Fat 1L", "Laban Full Fat 1L", "Almarai", barcode="6281007031119",
        brand_mappings=MAPPINGS, skip_cache=True, trace={})
    assert result["url"] == R_SECOND["url"]
    assert result["needs_review"] is False and result["unverified"] is False
    assert ran == ["6281007031119", "q1 almarai laban", "q2 almarai laban"]   # stops at the verified hit


def test_v1_returns_nothing_when_gemini_rejects_everything(monkeypatch):
    install_search(monkeypatch, {"q1 almarai laban": [R_TOP, R_SECOND]})
    install_downloads(monkeypatch)
    install_gemini(monkeypatch, {R_TOP["url"]: False, R_SECOND["url"]: False})
    result = image_search.search_best_product_image(
        "Almarai Laban Full Fat 1L", "Laban Full Fat 1L", "Almarai", brand_mappings=MAPPINGS,
        skip_cache=True, trace={})
    assert result is None


def test_v1_never_returns_a_raw_unfetched_search_result(monkeypatch):
    """Without a brand (no strict match) the old code returned the first raw search hit."""
    ran = install_search(monkeypatch, {"q1 almarai laban": [R_TOP], "Laban Full Fat bottle": [R_SECOND]})
    install_downloads(monkeypatch, failures={R_TOP["url"]})
    install_gemini(monkeypatch, {R_SECOND["url"]: False})
    result = image_search.search_best_product_image(
        "Laban Full Fat 1L", "Laban Full Fat 1L", "", skip_cache=True, trace={})
    assert "Laban Full Fat bottle" in ran          # the generic round ran and found only a rejected image
    assert result is None


# ---------------------------------------------------------------------------
# validate_image_via_gemini_vision fails closed (hotfix c)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, text=None):
        self.status_code = status
        self._text = text

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


@pytest.fixture
def gemini_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-test")
    monkeypatch.setattr(config, "ENABLE_GEMINI_PRE_VALIDATION", True)
    import local_cache_db
    monkeypatch.setattr(local_cache_db, "get_active_learning_clutter_flag", lambda brand: False)
    path = tmp_path / "img.jpg"
    path.write_bytes(BLOBS[R_TOP["url"]])
    return str(path)


def _post_returning(monkeypatch, resp, calls):
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers))
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(image_search.requests, "post", fake_post)


def test_validate_gemini_429_returns_none(monkeypatch, gemini_env):
    calls = []
    _post_returning(monkeypatch, _Resp(429), calls)
    assert image_search.validate_image_via_gemini_vision(gemini_env, "Laban 1L", "Almarai") is None
    [(url, headers)] = calls
    assert "key=" not in url and headers["x-goog-api-key"] == "secret-key"


@pytest.mark.parametrize("resp,expected", [
    (_Resp(200, '{"valid": true, "reason": "ok"}'), True),
    (_Resp(200, '{"valid": false, "reason": "competitor"}'), False),
    (_Resp(200, '{"valid": "false"}'), None),
    (_Resp(200, '{"valid": "true"}'), None),
    (_Resp(200, '[true]'), None),
    (_Resp(200, 'The image is valid.'), None),
    (_Resp(500), None),
    (_Resp(404), None),
    (TimeoutError("read timed out"), None),
])
def test_validate_gemini_parsing(monkeypatch, gemini_env, resp, expected):
    _post_returning(monkeypatch, resp, [])
    assert image_search.validate_image_via_gemini_vision(gemini_env, "Laban 1L", "Almarai") is expected


def test_validate_gemini_without_key_returns_none(monkeypatch, gemini_env):
    calls = []
    _post_returning(monkeypatch, _Resp(200, '{"valid": true}'), calls)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert image_search.validate_image_via_gemini_vision(gemini_env, "Laban 1L", "Almarai") is None
    assert calls == []


# ---------------------------------------------------------------------------
# SigLIP / Yandex (hotfixes d and f)
# ---------------------------------------------------------------------------

def test_siglip_without_model_returns_none(monkeypatch):
    monkeypatch.setattr(image_search, "get_siglip_model", lambda: (None, None))
    img = Image.open(io.BytesIO(BLOBS[R_TOP["url"]]))
    assert image_search.check_image_relevance_via_siglip(img, "Almarai", "Laban") == (None, None)


def test_yandex_scraper_removed(monkeypatch):
    assert not hasattr(image_search.ParallelConsensusScraper, "_fetch_yandex")
    assert not hasattr(image_search, "yandex_image_search")

    async def google(self, session, q):
        return [{"url": "https://g.ae/1.jpg", "title": "g", "width": 800, "height": 800}]

    async def bing(self, session, q):
        return [{"url": "https://b.ae/1.jpg", "title": "b", "width": 800, "height": 800}]

    async def ddg(self, q):
        return [{"url": "https://g.ae/1.jpg", "title": "g", "width": 800, "height": 800}]

    monkeypatch.setattr(image_search.ParallelConsensusScraper, "_fetch_google", google)
    monkeypatch.setattr(image_search.ParallelConsensusScraper, "_fetch_bing", bing)
    monkeypatch.setattr(image_search.ParallelConsensusScraper, "_fetch_duckduckgo", ddg)
    ranked = asyncio.run(image_search.ParallelConsensusScraper().aggregate_consensus_rankings("q"))
    assert [c["url"] for c in ranked] == ["https://g.ae/1.jpg", "https://b.ae/1.jpg"]
