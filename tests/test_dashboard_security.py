"""Dashboard hardening, through the Laravel app's HTTP kernel (a stub cli_bridge; Http::fake for the web; a fake DNS
resolver; no real network):

* /api/image-proxy fetches only from a host the system handed to the review screen (a stored candidate, a search
  result the dashboard relayed, the Sheet's image link, Cloudinary), over verified TLS, from public addresses only
  (the host and every redirect hop), at most 15 MB; its errors carry no exception text; images are cached privately
  for a day (immutable when the URL is content-addressed);
* /api/sheet/save and /api/sheet/preview forward only {spreadsheet_url, tab_name}, and refuse a value that could
  add keys to .env (the same rules as cli_bridge._sheet_inputs).
"""

import base64
import io
import json
import os
import subprocess
import tempfile

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, PHP, bridge_calls, sql, stub_env

ROW = 976400
SHEET = "https://docs.google.com/spreadsheets/d/" + "1Ab" * 10 + "/edit#gid=0"


def _png(size=(4, 4)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


def dashboard(env, requests_list, web=None, dns=None):
    """[{status, body, headers}] for [(method, uri, params)], and the URLs the app requested from the (fake) web.
    web: url -> {status, body (bytes), headers, size (a body of that many bytes), throw (a message)}; any other URL
    answers 404. dns: host -> addresses (else a public address)."""
    spec = {"requests": requests_list, "dns": dns or {}, "web": {}}
    for url, answer in (web or {}).items():
        answer = dict(answer)
        answer["body64"] = base64.b64encode(answer.pop("body", b"")).decode("ascii")
        spec["web"][url] = answer
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$kernel->bootstrap();
use Illuminate\\Support\\Facades\\Http;
$spec = json_decode(file_get_contents($argv[1]), true);
App\\Services\\ImageProxy::$resolver = function ($host) use ($spec) {{
    return $spec['dns'][strtolower($host)] ?? ['93.184.216.34'];
}};
$fakes = [];
foreach ($spec['web'] as $url => $a) {{
    if (isset($a['throw'])) {{
        $fakes[$url] = function () use ($a) {{ throw new Illuminate\\Http\\Client\\ConnectionException($a['throw']); }};
        continue;
    }}
    $body = isset($a['size']) ? str_repeat('a', $a['size']) : base64_decode($a['body64']);
    $fakes[$url] = Http::response($body, $a['status'] ?? 200, $a['headers'] ?? []);
}}
$fakes['*'] = Http::response('not found', 404);
Http::fake($fakes);
$out = [];
foreach ($spec['requests'] as $r) {{
    $request = Illuminate\\Http\\Request::create($r[0], $r[1], $r[2] ?? [], [], [], ['HTTP_ACCEPT' => 'application/json']);
    $response = $kernel->handle($request);
    $out[] = ['status' => $response->getStatusCode(), 'body64' => base64_encode((string) $response->getContent()),
              'cache' => $response->headers->get('Cache-Control'), 'type' => $response->headers->get('Content-Type')];
    $kernel->terminate($request, $response);
}}
$requested = [];
foreach (Http::recorded() as $pair) {{
    $requested[] = (string) $pair[0]->url();
}}
echo json_encode(['responses' => $out, 'requested' => $requested]);
"""
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
    for r in out["responses"]:
        r["body"] = base64.b64decode(r.pop("body64"))
    return out["responses"], out["requested"]


def proxy(url):
    from urllib.parse import quote

    return ["/api/image-proxy?url=" + quote(url, safe=""), "GET"]


@pytest.fixture
def candidate(mariadb_or_skip):
    """A stored candidate on cdn.store.example (its host is one the review screen shows)."""
    db = mariadb_or_skip
    sql(db, "DELETE FROM curation_candidates WHERE `row_number` = %s", (ROW,))
    sql(db, "INSERT INTO curation_candidates (`row_number`, product_name, brand, image_url) VALUES (%s, %s, %s, %s)",
        (ROW, "Milk 1L", "Almarai", "https://cdn.store.example/media/milk-1l.png"))
    yield db
    sql(db, "DELETE FROM curation_candidates WHERE `row_number` = %s", (ROW,))


# ---------------------------------------------------------------------------
# /api/image-proxy
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_a_stored_candidates_image_is_served_and_cached_privately(candidate, tmp_path):
    env, _calls = stub_env(tmp_path)
    url = "https://cdn.store.example/media/milk-1l.png"
    (res,), requested = dashboard(env, [proxy(url)], web={url: {"body": PNG, "headers": {"Content-Type": "image/png"}}})
    assert (res["status"], res["type"], res["body"]) == (200, "image/png", PNG)
    assert res["cache"] == "max-age=86400, private"            # Symfony sorts the directives
    assert requested == [url]


@NEEDS_LARAVEL
def test_a_host_the_system_never_showed_is_not_fetched(candidate, tmp_path):
    env, _calls = stub_env(tmp_path)
    urls = ["https://elsewhere.example/a.png", "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/api",
            "http://localhost/server-status"]
    responses, requested = dashboard(env, [proxy(u) for u in urls], web={u: {"body": PNG} for u in urls})
    assert [r["status"] for r in responses] == [403] * 4 and requested == []
    assert all(r["body"] == b"Image host not allowed" for r in responses)


@NEEDS_LARAVEL
def test_a_known_host_that_resolves_inside_is_refused(candidate, tmp_path):
    env, _calls = stub_env(tmp_path)
    url = "https://cdn.store.example/media/milk-1l.png"
    for inside in (["10.0.0.5"], ["93.184.216.34", "127.0.0.1"], ["::ffff:169.254.169.254"], ["fd00::7"],
                   ["100.64.3.4"]):
        (res,), requested = dashboard(env, [proxy(url)], web={url: {"body": PNG}}, dns={"cdn.store.example": inside})
        assert (res["status"], res["body"]) == (403, b"Image unavailable") and requested == [], inside
        assert res["cache"] == "no-store, private"


@NEEDS_LARAVEL
def test_every_redirect_hop_is_checked_and_a_public_one_is_followed(candidate, tmp_path):
    env, _calls = stub_env(tmp_path)
    start = "https://cdn.store.example/media/milk-1l.png"
    evil = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    (res,), requested = dashboard(env, [proxy(start)], web={
        start: {"status": 302, "headers": {"Location": evil}}, evil: {"body": PNG}})
    assert res["status"] == 403 and requested == [start]           # the metadata service is never asked
    moved = "https://img.other.example/m.png"
    (res,), requested = dashboard(env, [proxy(start)], web={
        start: {"status": 301, "headers": {"Location": moved}}, moved: {"body": PNG}})
    assert (res["status"], res["body"]) == (200, PNG) and requested == [start, moved]
    (res,), requested = dashboard(env, [proxy(start)], web={
        start: {"status": 302, "headers": {"Location": "/media/elsewhere.png"}},
        "https://cdn.store.example/media/elsewhere.png": {"status": 302, "headers": {"Location": "http://inside.example/x"}}},
        dns={"inside.example": ["192.168.1.1"]})
    assert res["status"] == 403 and requested == [start, "https://cdn.store.example/media/elsewhere.png"]


@NEEDS_LARAVEL
def test_a_body_over_15_mb_is_refused_and_errors_carry_no_exception_text(candidate, tmp_path):
    env, _calls = stub_env(tmp_path)
    big, declared, broken = ("https://cdn.store.example/" + n for n in ("big.png", "declared.png", "broken.png"))
    responses, _requested = dashboard(env, [proxy(big), proxy(declared), proxy(broken)], web={
        big: {"size": 15 * 1024 * 1024 + 1},
        declared: {"body": PNG, "headers": {"Content-Length": str(16 * 1024 * 1024)}},
        broken: {"throw": "cURL error 7: Failed to connect to 10.20.30.40 port 443 (internal detail)"}})
    assert [r["status"] for r in responses] == [413, 413, 502]
    assert all(r["body"] == b"Image unavailable" for r in responses)
    assert all(b"10.20.30.40" not in r["body"] and b"cURL" not in r["body"] for r in responses)


@NEEDS_LARAVEL
def test_cloudinary_needs_no_record_and_a_versioned_url_is_immutable(mariadb_or_skip, tmp_path):
    env, _calls = stub_env(tmp_path)
    url = "https://res.cloudinary.com/demo/image/upload/v1712345678/products/dairy/abc.png"
    (res,), _requested = dashboard(env, [proxy(url)], web={url: {"body": PNG}})
    assert res["status"] == 200 and res["cache"] == "immutable, max-age=86400, private"


@NEEDS_LARAVEL
def test_a_search_results_images_and_the_sheets_link_can_be_shown(mariadb_or_skip, tmp_path):
    fresh = "https://img.fresh.example/p/milk.png"
    sheet_link = "https://drive.sheetlinks.example/uc?id=abc"
    products = [{"row_number": 2, "product_name": "Milk 1L", "brand": "Almarai", "barcode": "",
                 "existing_image_link": sheet_link, "sheet_issues": []}]
    env, _calls = stub_env(tmp_path, products=products, extra={
        "search": {"status": "review", "decision": "REVIEW_UNSELECTED", "selected_image": None,
                   "candidates": [{"url": fresh, "status": "rejected"}]}})
    web = {fresh: {"body": PNG}, sheet_link: {"body": PNG}}
    before, _ = dashboard(env, [proxy(fresh), proxy(sheet_link)], web=web)
    assert [r["status"] for r in before] == [403, 403]
    responses, requested = dashboard(env, [["/api/search", "POST", {"product_name": "Milk 1L"}], proxy(fresh),
                                           ["/api/products-json", "GET"], proxy(sheet_link)], web=web)
    assert [r["status"] for r in responses] == [200, 200, 200, 200]
    assert requested == [fresh, sheet_link]


def test_the_proxy_no_longer_skips_tls_checks_or_echoes_exceptions():
    controller = (DASH / "app" / "Http" / "Controllers" / "ApiController.php").read_text(encoding="utf-8")
    body = controller.split("public function imageProxy", 1)[1].split("\n    }\n", 1)[0]
    assert "withoutVerifying" not in body and "getMessage" not in body
    service = (DASH / "app" / "Services" / "ImageProxy.php").read_text(encoding="utf-8")
    assert "withoutVerifying" not in service and "'allow_redirects' => false" in service


# ---------------------------------------------------------------------------
# /api/sheet/save and /api/sheet/preview
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_sheet_settings_forward_only_the_two_fields(mariadb_or_skip, tmp_path):
    env, calls = stub_env(tmp_path, extra={"sheet-save": {"status": "success"},
                                           "sheet-preview": {"status": "success", "headers": [], "rows": []}})
    sent = {"spreadsheet_url": f" {SHEET} ", "tab_name": "Products", "CLOUDINARY_URL": "x", "file_path": "/etc/x"}
    responses, _ = dashboard(env, [["/api/sheet/save", "POST", sent], ["/api/sheet/preview", "POST", sent]])
    assert [r["status"] for r in responses] == [200, 200]
    assert bridge_calls(calls) == [["sheet-save", {"spreadsheet_url": SHEET, "tab_name": "Products"}],
                                   ["sheet-preview", {"spreadsheet_url": SHEET, "tab_name": "Products"}]]


@NEEDS_LARAVEL
def test_sheet_values_that_could_add_env_keys_never_reach_the_bridge(mariadb_or_skip, tmp_path):
    env, calls = stub_env(tmp_path, extra={"sheet-save": {"status": "success"}})
    bad = [{"spreadsheet_url": SHEET + '"\nCLOUDINARY_URL="cloudinary://x', "tab_name": ""},
           {"spreadsheet_url": SHEET, "tab_name": 'T"\nDB_HOST="10.0.0.5'},
           {"spreadsheet_url": "https://evil.example/spreadsheets/d/" + "x" * 30},
           {"spreadsheet_url": "name with \\ backslash"},
           {"spreadsheet_url": ["a", "b"]}]
    responses, _ = dashboard(env, [["/api/sheet/save", "POST", b] for b in bad]
                             + [["/api/sheet/save", "POST", {"spreadsheet_url": ""}]])
    assert [r["status"] for r in responses] == [422] * 6
    assert [json.loads(r["body"]).get("error_code") for r in responses] == ["invalid_sheet"] * 5 + [None]
    assert json.loads(responses[-1]["body"])["error"] == "Spreadsheet URL or name is required"
    assert bridge_calls(calls) == []


def test_python_and_php_share_the_sheet_rules():
    import re

    import cli_bridge

    controller = (DASH / "app" / "Http" / "Controllers" / "ApiController.php").read_text(encoding="utf-8")
    php = re.search(r"SHEET_URL_RE = '~(.*)~D';", controller).group(1)
    # the PHP single-quoted literal: \' is a quote and \\\\ two backslashes (one escaped backslash in the regex)
    assert php.replace("\\'", "'").replace("\\\\\\\\", "\\\\").replace("$", r"\Z") == cli_bridge.SHEET_URL_RE.pattern \
        .replace('\\"', '"')
