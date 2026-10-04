"""Shared helpers for the dashboard tests of «why no pick», the run export and the sheet data quality card:
a stub cli_bridge (each call recorded, the export file written where the dashboard serves it), the Laravel
app through its HTTP kernel (a file download's body is what it sends) and the Run page script under node."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"
JS = DASH / "public" / "js"
PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not VENDOR_AUTOLOAD.exists(), reason="php or dashboard/vendor is missing")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
ROW = 976000
EXPORT_NAME = "laqta_run_2026-10-04_1530.json"


def sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


SHEET = [
    {"row_number": 2, "product_name": "MR JOHN FRENCH FRIES", "brand": "MR JOHN", "barcode": "",
     "sheet_issues": [{"key": "no_size", "text": "الحجم ناقص بالشيت"}, {"key": "no_barcode", "text": "الباركود ناقص بالشيت"}]},
    {"row_number": 3, "product_name": "TARGET CHICEKN LUNCHEN MEAT", "brand": "TARGET", "barcode": "",
     "sheet_issues": [{"key": "no_barcode", "text": "الباركود ناقص بالشيت"},
                      {"key": "brand_unknown", "brand": "TARGET", "empty": False,
                       "text": "الماركة «TARGET» مش موجودة في Brands Mapping"},
                      {"key": "typo", "word": "CHICEKN", "suggest": "CHICKEN", "text": "يمكن «CHICEKN» قصدك «CHICKEN»"},
                      {"key": "typo", "word": "LUNCHEN", "suggest": "LUNCHEON", "text": "يمكن «LUNCHEN» قصدك «LUNCHEON»"}]},
    {"row_number": 4, "product_name": "ALMARAI MILK 1L", "brand": "ALMARAI", "barcode": "6281007035309",
     "sheet_issues": [{"key": "duplicate_barcode", "rows": [5], "text": "نفس الباركود مكتوب لمنتج ثاني (صف 5)"}]},
    {"row_number": 5, "product_name": "ALMARAI LABAN 1L", "brand": "ALMARAI", "barcode": "6281007035309",
     "sheet_issues": [{"key": "duplicate_barcode", "rows": [4], "text": "نفس الباركود مكتوب لمنتج ثاني (صف 4)"}]},
    {"row_number": 6, "product_name": "ALMARAI WATER 1.5L", "brand": "ALMARAI", "barcode": "6281007035316",
     "sheet_issues": []},
]
OLD_SHEET = [{k: v for k, v in p.items() if k != "sheet_issues"} for p in SHEET]


def stub_env(tmp_path, products=SHEET, export=None):
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "stub_bridge.py"
    exports = str(ROOT / "temp" / "exports")
    fixture = {"get_products": {"status": "success", "products": products},
               "explain_backfill": {"status": "success", "filled": 4, "checked": 5},
               "export_run": export or {"status": "success", "file": EXPORT_NAME, "rows": 3}}
    stub.write_text(
        "import base64, json, os, sys\n"
        f"FIXTURE = json.loads({json.dumps(json.dumps(fixture, ensure_ascii=False))})\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "params = json.loads(base64.b64decode(sys.argv[2]).decode('utf-8')) if len(sys.argv) > 2 else {}\n"
        f"open({str(calls)!r}, 'a').write(json.dumps([action, params]) + '\\n')\n"
        "out = FIXTURE.get(action, {'status': 'error', 'error': 'unexpected action'})\n"
        "if action == 'export_run' and out.get('status') == 'success':\n"
        f"    os.makedirs({exports!r}, exist_ok=True)\n"
        f"    open(os.path.join({exports!r}, out['file']), 'w', encoding='utf-8').write(json.dumps({{'format': 'smoke_live/2', 'rows': []}}))\n"
        "print(json.dumps(out, ensure_ascii=False))\n", encoding="utf-8")
    compiled = tmp_path / "views"
    compiled.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing", "APP_KEY": "base64:" + "A" * 43 + "=", "APP_DEBUG": "true", "SESSION_DRIVER": "array",
        "CACHE_STORE": "array", "LOG_CHANNEL": "stderr", "VIEW_COMPILED_PATH": str(compiled),
        "DB_CONNECTION": "mariadb", "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"), "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": os.environ["DB_DATABASE"], "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""), "CLI_BRIDGE_PATH": str(stub), "PYTHON_PATH": sys.executable,
    })
    return env, calls


def kernel(env, requests_list):
    """[{status, headers, body}] for [(method, uri)]; a file download's body is what it sends."""
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as [$method, $uri]) {{
    $request = Illuminate\\Http\\Request::create($uri, $method, [], [], [], ['HTTP_ACCEPT' => 'application/json']);
    $response = $kernel->handle($request);
    if ($response instanceof Symfony\\Component\\HttpFoundation\\BinaryFileResponse) {{
        ob_start();
        $response->sendContent();
        $body = ob_get_clean();
    }} else {{
        $body = $response->getContent();
    }}
    $out[] = ['status' => $response->getStatusCode(), 'body' => $body,
              'disposition' => $response->headers->get('Content-Disposition'),
              'type' => $response->headers->get('Content-Type'), 'rows' => $response->headers->get('X-Laqta-Rows')];
    $kernel->terminate($request, $response);
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path, json.dumps(requests_list)], cwd=DASH, env=env, capture_output=True,
                                text=True, timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def bridge_calls(calls):
    return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []


RUN_DOM = (ROOT / "tests" / "test_laqta_run.py").read_text(encoding="utf-8").split('RUN_DOM = r"""', 1)[1].split('"""', 1)[0]


def run_page(script):
    source = ("globalThis.window = globalThis;\n" + RUN_DOM + (JS / "run-common.js").read_text(encoding="utf-8") + "\n"
              + (JS / "run.js").read_text(encoding="utf-8") + "\n" + script)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(source)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])
