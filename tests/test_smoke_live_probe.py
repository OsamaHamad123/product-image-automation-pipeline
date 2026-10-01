"""scripts/smoke_live.py --probe: one cheap read-only call per configured service, a plain-words table,
and never a key (or any piece of one) in what it prints or writes. HTTP is a recording double and every
socket is refused, so nothing here reaches a real service."""

import argparse
import importlib.util
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO = Path(__file__).resolve().parents[1]


def _host(url: str) -> str:
    """The URL's host name: the fake HTTP routes match services by host, not by substring."""
    return (urlsplit(url).hostname or "").lower()


def _load_script():
    spec = importlib.util.spec_from_file_location("smoke_live_probe_under_test", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_key(prefix):
    # built at run time (no key-shaped literal in the repository)
    return prefix + uuid.uuid4().hex + uuid.uuid4().hex[:8]


@pytest.fixture
def keys():
    return {
        "SERPER_API_KEY": _fake_key("srp"),
        "GEMINI_API_KEY": _fake_key("AIza"),
        "SERPAPI_API_KEY": _fake_key("spa"),
        "ANTHROPIC_API_KEY": _fake_key("sk-ant-"),
        "PHOTOROOM_API_KEY": _fake_key("pr"),
        "CLOUDINARY_API_KEY": _fake_key("cl"),
        "CLOUDINARY_API_SECRET": _fake_key("cs"),
    }


class FakeResponse:
    def __init__(self, status=200, data=None, text=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text if text is not None else json.dumps(self._data)

    def json(self):
        return self._data


class FakeHttp:
    """Answers by URL; records every call. route(method, url, kwargs) -> FakeResponse or raises."""

    def __init__(self, route):
        self.route = route
        self.calls = []

    def _do(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.route(method, url, kwargs)

    def get(self, url, **kwargs):
        return self._do("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._do("post", url, **kwargs)


def ok_route(keys):
    def route(method, url, kwargs):
        if "google.serper.dev/images" in url:
            return FakeResponse(200, {"images": [{"imageUrl": "https://x/1.jpg"}] * 7})
        if "google.serper.dev/search" in url:
            return FakeResponse(200, {"organic": [{"link": "https://www.luluhypermarket.com/p/1"}] * 4})
        if "google.serper.dev/shopping" in url:
            return FakeResponse(200, {"shopping": [{"title": "Almarai"}] * 5})
        if "google.serper.dev/lens" in url:
            return FakeResponse(200, {"organic": [{"title": "match"}] * 3})
        if "serpapi.com/account.json" in url:
            # SerpApi's account answer carries the key itself: it must never reach the output
            return FakeResponse(200, {"api_key": keys["SERPAPI_API_KEY"], "plan_name": "Developer",
                                      "total_searches_left": 4870, "account_email": "owner@example.com"})
        if _host(url) == "generativelanguage.googleapis.com":
            return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
        if url.endswith("/v1/messages"):
            return FakeResponse(200, {"content": [{"type": "text", "text": "OK"}]})
        if url.endswith("/v1/models"):
            return FakeResponse(200, {"data": [{"id": "claude-haiku-4-5"}]})
        if _host(url) == "image-api.photoroom.com":
            return FakeResponse(200, {"images": {"available": 812, "subscription": 1000}})
        if _host(url) == "api.cloudinary.com":
            return FakeResponse(200, {"status": "ok"})
        raise AssertionError(f"unexpected URL {url}")
    return route


def _lookup(values):
    return lambda name: values.get(name, "")


def _assert_no_key(text, keys):
    for key in keys.values():
        assert key not in text
        for i in range(len(key) - 5):
            assert key[i:i + 6] not in text, "a piece of a key was printed"


def _all_settings(keys, **extra):
    values = dict(keys, CLOUDINARY_CLOUD_NAME="demo-cloud", GEMINI_MODEL="gemini-3.1-flash-lite",
                  VERIFIER_STRONG="claude:claude-haiku-4-5", VISUAL_SEARCH="auto", EXPANSION_ENABLED="true",
                  EXPANSION_MAX_CALLS="4")
    values.update(extra)
    return values


def test_everything_configured_works_and_no_key_is_printed(keys, offline, tmp_path, capsys):
    script = _load_script()
    http = FakeHttp(ok_route(keys))
    values = _all_settings(keys)
    probe = script.Probe(http=http, lookup=_lookup(values))
    results = probe.run()
    by = {r["service"]: r for r in results}
    assert set(by) == {"serper_images", "serper_web", "serper_shopping", "serper_lens", "serpapi_lens",
                       "primary_model", "strong_model", "anthropic", "photoroom", "cloudinary"}
    assert all(r["status"] == "works" for r in results), [(r["service"], r["status"], r["reason"]) for r in results]
    assert by["serper_images"]["reason"].startswith("7 images")
    assert "site: search accepted" in by["serper_web"]["reason"]
    assert "4870 searches left" in by["serpapi_lens"]["reason"]
    assert "812 images left" in by["photoroom"]["reason"]
    assert "gemini-3.1-flash-lite" in by["primary_model"]["label"]
    assert "claude-haiku-4-5" in by["strong_model"]["label"]

    # keys travel in headers / auth / params, never in a URL the probe builds itself
    for call in http.calls:
        _assert_no_key(call["url"], keys)
    gemini = next(c for c in http.calls if "generativelanguage" in c["url"])
    assert gemini["headers"]["x-goog-api-key"] == keys["GEMINI_API_KEY"]
    serpapi = next(c for c in http.calls if _host(c["url"]) == "serpapi.com")
    assert serpapi["method"] == "get" and serpapi["params"] == {"api_key": keys["SERPAPI_API_KEY"]}
    cloud = next(c for c in http.calls if "cloudinary" in c["url"])
    assert cloud["method"] == "get" and cloud["url"].endswith("/demo-cloud/ping")
    # read-only: nothing but a GET on the image host, the storage and the accounts
    assert {c["method"] for c in http.calls if any(h in c["url"] for h in ("cloudinary", "photoroom", "serpapi"))} \
        == {"get"}

    table = script.format_probe(results)
    _assert_no_key(table + json.dumps(results), keys)
    assert "10 work, 0 failed" in table


def test_unconfigured_services_are_not_errors_and_make_no_call(offline):
    script = _load_script()
    http = FakeHttp(lambda *a: pytest.fail("no call may be made without a key"))
    results = script.Probe(http=http, lookup=_lookup({"GEMINI_MODEL": "gemini-3.1-flash-lite"})).run()
    statuses = {r["service"]: r["status"] for r in results}
    assert http.calls == []
    assert "failed" not in statuses.values()
    assert statuses["serper_images"] == statuses["primary_model"] == "not configured"
    assert statuses["anthropic"] == statuses["photoroom"] == statuses["cloudinary"] == "not configured"
    assert statuses["strong_model"] == "not configured"      # no VERIFIER_STRONG in this version's settings


def test_switches_turn_services_off_without_calls(keys, offline):
    script = _load_script()
    http = FakeHttp(ok_route(keys))
    values = _all_settings(keys, EXPANSION_ENABLED="false", VERIFIER_STRONG="off")
    results = {r["service"]: r for r in script.Probe(http=http, lookup=_lookup(values)).run()}
    for service in ("serper_web", "serper_shopping", "serper_lens", "serpapi_lens"):
        assert results[service]["status"] == "off" and "expansion round is off" in results[service]["reason"]
    assert results["strong_model"]["status"] == "off"
    assert not [c for c in http.calls if any(p in c["url"] for p in ("/search", "/shopping", "/lens", "serpapi"))]

    values = _all_settings(keys, VISUAL_SEARCH="serper")
    results = {r["service"]: r for r in script.Probe(http=FakeHttp(ok_route(keys)), lookup=_lookup(values)).run()}
    assert results["serpapi_lens"]["status"] == "off"
    assert "VISUAL_SEARCH is 'serper'" in results["serpapi_lens"]["reason"]
    assert results["serper_lens"]["status"] == "works"


def test_strong_model_equal_to_primary_is_not_called_twice(keys, offline):
    script = _load_script()
    http = FakeHttp(ok_route(keys))
    values = _all_settings(keys, VERIFIER_STRONG="gemini:gemini-3.1-flash-lite")
    results = {r["service"]: r for r in script.Probe(http=http, lookup=_lookup(values)).run()}
    assert results["strong_model"]["status"] == "works"
    assert results["strong_model"]["reason"] == "same model as the primary reader"
    assert sum(1 for c in http.calls if "generativelanguage" in c["url"]) == 1


def test_failures_are_explained_in_plain_words_without_keys(keys, offline):
    import requests

    script = _load_script()

    def route(method, url, kwargs):
        if "serper.dev/images" in url:
            return FakeResponse(403, text='{"message": "Unauthorized", "key": "%s"}' % keys["SERPER_API_KEY"])
        if "serper.dev/search" in url:
            return FakeResponse(400, text='{"message": "Not enough credits"}')
        if "serper.dev/shopping" in url:
            return FakeResponse(429, text="rate limited")
        if "serper.dev/lens" in url:
            return FakeResponse(404, text="Not Found")
        if _host(url) == "serpapi.com":
            # requests puts the whole URL (with ?api_key=) into its exception text
            raise requests.ConnectionError(f"Max retries exceeded with url: /account.json?api_key="
                                           f"{kwargs['params']['api_key']}")
        if "generativelanguage" in url:
            return FakeResponse(429, text='{"error": {"status": "RESOURCE_EXHAUSTED"}}')
        if url.endswith("/v1/messages"):
            return FakeResponse(400, text='{"error": {"message": "Your credit balance is too low"}}')
        if url.endswith("/v1/models"):
            return FakeResponse(401, text='{"error": {"type": "authentication_error"}}')
        if "photoroom" in url:
            raise requests.Timeout("read timed out")
        if "cloudinary" in url:
            return FakeResponse(401, text=f"Invalid api_key {keys['CLOUDINARY_API_KEY']}")
        raise AssertionError(url)

    values = _all_settings(keys)
    results = {r["service"]: r for r in script.Probe(http=FakeHttp(route), lookup=_lookup(values)).run()}
    assert all(r["status"] == "failed" for r in results.values()), results
    assert "Serper key was rejected" in results["serper_images"]["reason"]
    assert "no Serper credit left" in results["serper_web"]["reason"]
    assert "try again later" in results["serper_shopping"]["reason"]
    assert results["serper_lens"]["reason"] == "visual search (Lens) is not on your Serper plan"
    assert results["serpapi_lens"]["reason"] == "could not connect (no internet, DNS or firewall)"
    assert "free-tier" in results["primary_model"]["reason"]
    assert "no Anthropic credit left" in results["strong_model"]["reason"]
    assert "Anthropic key was rejected" in results["anthropic"]["reason"]
    assert results["photoroom"]["reason"].startswith("no answer in time")
    assert "key or secret was rejected" in results["cloudinary"]["reason"]
    _assert_no_key(script.format_probe(list(results.values())) + json.dumps(list(results.values())), keys)


def test_a_bad_model_id_and_a_missing_claude_key(keys, offline):
    script = _load_script()
    values = _all_settings(keys, VERIFIER_STRONG="gpt:4o", VERIFIER_PRIMARY="claude:claude-sonnet-5-5")
    values.pop("ANTHROPIC_API_KEY")
    results = {r["service"]: r for r in script.Probe(http=FakeHttp(ok_route(keys)), lookup=_lookup(values)).run()}
    assert results["primary_model"]["status"] == "not configured"
    assert "ANTHROPIC_API_KEY" in results["primary_model"]["reason"]
    assert results["strong_model"]["status"] == "failed" and "not a model id" in results["strong_model"]["reason"]
    assert results["anthropic"]["status"] == "not configured"


def test_a_key_pasted_into_a_model_setting_is_never_echoed(keys, offline, monkeypatch):
    """A key pasted into VERIFIER_STRONG by mistake (.env) is not a configured secret, so redaction cannot
    catch it: the probe and the dry run's header must never show a setting that is not a model id."""
    script = _load_script()
    pasted = _fake_key("sk-ant-api03-")
    values = _all_settings(keys, VERIFIER_STRONG=pasted, VERIFIER_PRIMARY="gemini:" + _fake_key("AIza")[:20] + "/x")
    values.pop("ANTHROPIC_API_KEY")
    results = {r["service"]: r for r in script.Probe(http=FakeHttp(ok_route(keys)), lookup=_lookup(values)).run()}
    assert results["strong_model"]["status"] == "failed"
    assert "VERIFIER_STRONG is not a model id" in results["strong_model"]["reason"]
    assert "VERIFIER_PRIMARY is not a model id" in results["primary_model"]["reason"]
    text = script.format_probe(list(results.values())) + json.dumps(list(results.values()))
    _assert_no_key(text, {"pasted": pasted, "primary": values["VERIFIER_PRIMARY"].split(":", 1)[1]})

    monkeypatch.setattr(script, "accessor", lambda name, fallback=None: {
        "verifier_primary": "gemini:gemini-3.1-flash-lite", "verifier_strong": pasted}.get(name))
    settings = type("S", (), {"gemini_model": staticmethod(lambda: "gemini-3.1-flash-lite"),
                              "auto_publish_enabled": staticmethod(lambda: False)})
    pipeline = type("P", (), {"find_product_image": staticmethod(lambda spec, **kw: None)})
    args = argparse.Namespace(rows=["2"], serp_cost=0.001, vlm_cost=0.001)
    meta = script.run_meta(args, settings, pipeline, True)
    assert meta["verifier_primary"] == "gemini:gemini-3.1-flash-lite"
    assert meta["verifier_strong"] == "(not a model id)"
    _assert_no_key(json.dumps(meta), {"pasted": pasted})


def test_run_probe_prints_the_table_writes_json_and_exits_1_on_a_failure(keys, offline, tmp_path, capsys,
                                                                         monkeypatch):
    script = _load_script()
    values = _all_settings(keys)
    monkeypatch.setattr(script, "setting", lambda name, default="": values.get(name, default))

    def route(method, url, kwargs):
        if "cloudinary" in url:
            return FakeResponse(404, text="cloud not found")
        return ok_route(keys)(method, url, kwargs)

    out = tmp_path / "probe.json"
    args = argparse.Namespace(probe_image=script.PROBE_IMAGE, serp_cost=0.001, json=str(out))
    code = script.run_probe(args, http=FakeHttp(route))
    printed = capsys.readouterr().out
    assert code == 1
    assert "Cloudinary (image hosting)" in printed and "cloud name was not found" in printed
    assert "9 work, 1 failed" in printed
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["format"] == "smoke_live_probe/1" and len(doc["services"]) == 10
    _assert_no_key(printed + out.read_text(encoding="utf-8"), keys)

    code = script.run_probe(argparse.Namespace(probe_image=script.PROBE_IMAGE, serp_cost=0.001, json=None),
                            http=FakeHttp(ok_route(keys)))
    assert code == 0


def test_main_probe_needs_no_rows_and_a_dry_run_needs_rows(monkeypatch):
    script = _load_script()
    seen = []
    monkeypatch.setattr(script, "run_probe", lambda args, **kw: seen.append(args) or 0)
    assert script.main(["--probe"]) == 0 and len(seen) == 1
    with pytest.raises(SystemExit):
        script.main([])


def test_redact_hides_secret_values_and_query_keys():
    script = _load_script()
    key = _fake_key("k")
    text = f"HTTPSConnectionPool: /search.json?engine=google_lens&api_key={key}&url=x ({key})"
    clean = script.redact(text, secrets=[key])
    assert key not in clean and "api_key=[hidden]" in clean
    # a whole --json document keeps harmless 'key=' parameters of image URLs
    url = "https://cdn.example.com/img.jpg?cachekey=abc123"
    assert script.redact(url, secrets=[key], query_keys=False) == url
