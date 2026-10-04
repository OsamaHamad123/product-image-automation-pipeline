"""
Approvals that publish nothing (owner's bulk approval of 2026-10-04: 11 of 11 «ما انعتمدت» with no reason).

* image_processor._download_bytes: the publish-time re-download goes direct first and through PROXY_URL only after a
  failed or refused attempt, like catalog_match.fetch. It used to go through the proxy alone whenever one was set,
  so a slow or dead proxy failed every approval.
* image_processor._load_from_candidate_store reads a relative CANDIDATE_STORE_DIR from the project folder, like
  catalog_match.fetch writes it, whatever the process's working directory.
* review/core.js plainError: every image code an approval can fail with has an Arabic sentence (the raw code stays in
  the tooltip), instead of the bare «ما انعتمدت.».
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

import image_processor
from http_client import FetchResult
from laqta_review_harness import NODE, run as harness_run

ROOT = Path(__file__).resolve().parents[1]

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeClient:
    """http_client.ImpersonateClient stand-in: records the route of every download."""

    calls = []
    results = {}

    def __init__(self, use_proxy=False, proxy_url=None, **_kwargs):
        self.route = "proxy" if use_proxy and proxy_url else "direct"

    def fetch_image(self, url, timeout=15, max_bytes=0, referer=None, headers=None):
        FakeClient.calls.append(self.route)
        return FakeClient.results[self.route]


@pytest.fixture
def client(monkeypatch):
    import http_client
    FakeClient.calls = []
    FakeClient.results = {}
    monkeypatch.setattr(http_client, "ImpersonateClient", FakeClient)
    return FakeClient


def _proxy(monkeypatch, url):
    monkeypatch.setattr(image_processor.settings, "proxy_url", lambda: url)


def test_the_first_download_is_direct_even_with_a_proxy_set(client, monkeypatch):
    _proxy(monkeypatch, "http://proxy.example:8080")
    client.results = {"direct": FetchResult(content=PNG, status=200, content_type="image/png")}
    assert image_processor._download_bytes("https://cdn.example/a.png") == (PNG, None)
    assert client.calls == ["direct"]


@pytest.mark.parametrize("error", ["timeout", "connection_error", "http_403", "http_429", "http_503"])
def test_a_failed_or_refused_direct_download_retries_through_the_proxy(client, monkeypatch, error):
    _proxy(monkeypatch, "http://proxy.example:8080")
    client.results = {"direct": FetchResult(error=error),
                      "proxy": FetchResult(content=PNG, status=200, content_type="image/png")}
    assert image_processor._download_bytes("https://cdn.example/a.png") == (PNG, None)
    assert client.calls == ["direct", "proxy"]


@pytest.mark.parametrize("error", ["http_404", "not_image", "too_large"])
def test_an_answer_the_proxy_cannot_change_is_not_retried(client, monkeypatch, error):
    _proxy(monkeypatch, "http://proxy.example:8080")
    client.results = {"direct": FetchResult(error=error)}
    assert image_processor._download_bytes("https://cdn.example/a.png") == (None, f"download_{error}")
    assert client.calls == ["direct"]


def test_without_a_proxy_a_failed_download_is_reported_once(client, monkeypatch):
    _proxy(monkeypatch, "")
    client.results = {"direct": FetchResult(error="timeout")}
    assert image_processor._download_bytes("https://cdn.example/a.png") == (None, "download_timeout")
    assert client.calls == ["direct"]


def test_both_routes_failing_report_the_proxy_attempts_error(client, monkeypatch):
    _proxy(monkeypatch, "http://proxy.example:8080")
    client.results = {"direct": FetchResult(error="http_403"), "proxy": FetchResult(error="connection_error")}
    assert image_processor._download_bytes("https://cdn.example/a.png") == (None, "download_connection_error")
    assert client.calls == ["direct", "proxy"]


def test_a_relative_candidate_store_is_read_from_the_project_folder(tmp_path, monkeypatch):
    sha = hashlib.sha256(PNG).hexdigest()
    rel = os.path.join("temp", "test_approval_store")
    store = ROOT / rel
    store.mkdir(parents=True, exist_ok=True)
    try:
        (store / f"{sha}.png").write_bytes(PNG)
        monkeypatch.setattr(image_processor.settings, "candidate_store_dir", lambda: rel)
        monkeypatch.chdir(tmp_path)          # the process runs from another folder
        assert image_processor._load_from_candidate_store(sha) == PNG
    finally:
        shutil.rmtree(store, ignore_errors=True)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_image_code_an_approval_fails_with_has_an_arabic_sentence(tmp_path):
    codes = ["source_changed", "download_timeout", "download_connection_error", "download_http_503", "download_failed",
             "download_http_403", "download_http_429", "download_http_404", "download_not_image", "download_too_large",
             "download_bad_scheme", "not_image", "image_too_large", "source_too_large", "source_missing",
             "source_not_found", "source_unreadable", "processing_failed", "upload_failed", "sheet_write_failed",
             "something_new"]
    texts = harness_run(f"out.texts = {json.dumps(codes)}.map(c => R.plainError(c, 'ما انعتمدت.'));",
                        tmp_path, {"products": [], "queue": {"status": "success", "ready_for_review": 0, "rows": []}})
    out = dict(zip(codes, texts["texts"]))
    for code in codes[:-1]:
        assert out[code] != "ما انعتمدت.", code          # a reason, not the bare fallback
        assert code not in out[code], code              # the code itself stays in the tooltip only
    assert out["source_changed"].startswith("الصورة على موقع المتجر تغيّرت")
    assert out["download_timeout"].startswith("ما قدرنا ننزّل الصورة")
    assert out["download_http_403"].startswith("موقع المتجر رفض تنزيل الصورة")
    assert out["download_http_404"].startswith("الصورة انشالت")
    assert out["not_image"].startswith("الرابط ما عاد صورة صالحة")
    # a code the page does not know yet keeps the caller's fallback
    assert out["something_new"] == "ما انعتمدت."
