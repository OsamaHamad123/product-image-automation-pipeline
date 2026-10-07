"""/api/image-proxy behind a store CDN that blocks the server (ImageProxy::fetch, through the Laravel kernel with
Http::fake; no real network): Carrefour's Akamai answers a data-centre address with 403 and any non-browser with an
empty HTML page, and blocks some of the residential proxy's exit addresses too.

* a refusal (403 / a page instead of an image) is fetched again through the proxy, up to PROXY_ATTEMPTS times;
* without a proxy, or when every proxy try fails, the direct answer stands (same status and body as before);
* an answer the proxy cannot change (404) is not retried.
"""

import base64
import io
import json
import os
import subprocess
import tempfile

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, PHP, sql, stub_env

pytestmark = NEEDS_LARAVEL

URL = "https://cdn.mafr.example/pim-content/1163628_main.jpg"
CHALLENGE = b"<!DOCTYPE html>\n<html>\n<body>\n<p></p>\n</body>\n</html>"


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


def fetch(env, answers):
    """(ImageProxy::fetch result, number of requests made) for answers = [(status, body, content_type)], in order."""
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Http\\Kernel::class)->bootstrap();
use Illuminate\\Support\\Facades\\Http;
$answers = json_decode(file_get_contents($argv[1]), true);
App\\Services\\ImageProxy::$resolver = fn ($host) => ['93.184.216.34'];
$sequence = Http::sequence();
foreach ($answers as [$status, $body64, $type]) {{
    $sequence->push(base64_decode($body64), $status, ['Content-Type' => $type]);
}}
Http::fake(['*' => $sequence]);
$out = App\\Services\\ImageProxy::fetch('{URL}');
echo json_encode(['status' => $out['status'], 'error' => $out['error'] ?? null,
                  'body64' => isset($out['body']) ? base64_encode($out['body']) : null,
                  'requests' => count(Http::recorded())]);
"""
    spec = [[s, base64.b64encode(b).decode("ascii"), t] for s, b, t in answers]
    with tempfile.TemporaryDirectory() as folder:
        path, data = os.path.join(folder, "run.php"), os.path.join(folder, "spec.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        result = subprocess.run([PHP, path, data], cwd=DASH, env=env, capture_output=True, text=True, timeout=240,
                                encoding="utf-8")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    out = json.loads(result.stdout)
    out["body"] = base64.b64decode(out.pop("body64")) if out["body64"] is not None else None
    return out


@pytest.fixture
def env(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    try:
        sql(db, "DELETE FROM system_settings WHERE `key` = 'proxy_url'")      # the env decides in these tests
    except Exception:  # noqa: BLE001 - no settings table in this database
        pass
    environ, _ = stub_env(tmp_path)
    return environ


def with_proxy(env):
    return dict(env, PROXY_URL="http://user:pass@gate.proxy.example:7000")


def test_a_challenge_page_then_a_blocked_address_then_the_image(env):
    out = fetch(with_proxy(env), [(200, CHALLENGE, "text/html"), (403, b"Access Denied", "text/html"),
                                  (200, PNG, "image/png")])
    assert (out["status"], out["body"]) == (200, PNG)
    assert out["requests"] == 3                     # direct, proxy address 1 (refused), proxy address 2


def test_a_direct_403_goes_through_the_proxy(env):
    out = fetch(with_proxy(env), [(403, b"Access Denied", "text/html"), (200, PNG, "image/png")])
    assert (out["status"], out["body"], out["requests"]) == (200, PNG, 2)


def test_the_proxy_is_tried_at_most_three_times_then_the_direct_answer_stands(env):
    refused = (403, b"Access Denied", "text/html")
    out = fetch(with_proxy(env), [refused] * 6)
    assert (out["status"], out["error"]) == (403, "upstream")
    assert out["requests"] == 1 + 3


def test_without_a_proxy_nothing_changes(env):
    env = dict(env, PROXY_URL="")
    out = fetch(env, [(403, b"Access Denied", "text/html"), (200, PNG, "image/png")])
    assert (out["status"], out["requests"]) == (403, 1)
    page = fetch(env, [(200, CHALLENGE, "text/html")])
    assert (page["status"], page["body"], page["requests"]) == (200, CHALLENGE, 1)   # the controller answers 415


def test_a_404_is_not_retried_and_an_image_is_not_fetched_twice(env):
    missing = fetch(with_proxy(env), [(404, b"Not Found", "text/html"), (200, PNG, "image/png")])
    assert (missing["status"], missing["requests"]) == (404, 1)
    direct = fetch(with_proxy(env), [(200, PNG, "image/png")])
    assert (direct["body"], direct["requests"]) == (PNG, 1)
