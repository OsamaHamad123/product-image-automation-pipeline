"""POST /api/upload_manual_image checks the file in Laravel before anything reaches Python (ApiController):

* only an image by its content (JPG, PNG, WEBP), at most 20 MB; anything else answers 422 with an Arabic message and
  the bridge is never called;
* the stored file's extension comes from the checked content type, never from the name the browser sent.
"""

import base64
import json
import os
import subprocess
import tempfile

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, PHP, bridge_calls, stub_env

pytestmark = NEEDS_LARAVEL

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
INVALID = "الملف لازم يكون صورة JPG أو PNG أو WEBP، وحجمه أقل من 20MB."
FIELDS = {"row_number": "12", "product_name": "Almarai Milk 1L", "brand": "Almarai", "sku_key": "ajd-key-12"}


def upload(env, path, client_name):
    """{status, body} of one upload of the file at `path`, named `client_name` by the browser."""
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$spec = json_decode(file_get_contents($argv[1]), true);
$file = new Illuminate\\Http\\UploadedFile($spec['path'], $spec['name'], null, null, true);
$request = Illuminate\\Http\\Request::create('/api/upload_manual_image', 'POST', $spec['fields'], [], ['file' => $file],
                                            ['HTTP_ACCEPT' => 'application/json', 'REMOTE_ADDR' => '203.0.113.9']);
$response = $kernel->handle($request);
echo json_encode(['status' => $response->getStatusCode(), 'body' => (string) $response->getContent()], JSON_UNESCAPED_UNICODE);
$kernel->terminate($request, $response);
"""
    with tempfile.TemporaryDirectory() as folder:
        php, spec = os.path.join(folder, "upload.php"), os.path.join(folder, "spec.json")
        with open(php, "w", encoding="utf-8") as fh:
            fh.write(script)
        with open(spec, "w", encoding="utf-8") as fh:
            json.dump({"path": str(path), "name": client_name, "fields": FIELDS}, fh)
        result = subprocess.run([PHP, php, spec], cwd=DASH, env=env, capture_output=True, text=True, timeout=240,
                                encoding="utf-8")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    out = json.loads(result.stdout)
    out["json"] = json.loads(out["body"])
    return out


@pytest.fixture
def env(tmp_path):
    environ, calls = stub_env(tmp_path, extra={"upload_manual_image": {"status": "success",
                                                                        "image_link": "https://res.cloudinary.com/x/u.png"}})
    return environ, calls


def test_a_png_named_anything_is_stored_with_the_png_extension(env, tmp_path):
    environ, calls = env
    path = tmp_path / "photo.exe"
    path.write_bytes(PNG)
    out = upload(environ, path, "photo.exe")
    assert out["status"] == 200, out["body"]
    sent = bridge_calls(calls)
    assert [a for a, _ in sent] == ["upload_manual_image"]
    assert sent[0][1]["file_path"].endswith(".png") and sent[0][1]["sku_key"] == "ajd-key-12"


@pytest.mark.parametrize("content,name", [(b"just some text, not a picture\n", "label.png"),
                                          (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n", "scan.jpg")])
def test_a_file_that_is_not_an_image_is_refused_before_python(env, tmp_path, content, name):
    environ, calls = env
    path = tmp_path / name
    path.write_bytes(content)
    out = upload(environ, path, name)
    assert out["status"] == 422
    assert out["json"]["error"] == INVALID and out["json"]["status"] == "error"
    assert bridge_calls(calls) == []


def test_an_image_over_20mb_is_refused_before_python(env, tmp_path):
    environ, calls = env
    path = tmp_path / "huge.png"
    path.write_bytes(PNG + b"\0" * (20 * 1024 * 1024 + 1024))
    out = upload(environ, path, "huge.png")
    assert out["status"] == 422 and out["json"]["error"] == INVALID
    assert bridge_calls(calls) == []
