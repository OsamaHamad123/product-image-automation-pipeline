"""The nightly logs are visible where the dashboard shows pipeline.log (package P4b, item 5).

GET /api/view-nightly-log (HealthController::nightlyLog) shows the newest temp/nightly/nightly_YYYY-MM-DD.log, or
the night given as ?date=YYYY-MM-DD, with the same tail and secret masking as the pipeline log. The request names a
date, never a file: anything else is refused (400), and the file read must stay inside temp/nightly after symlinks
are resolved. The health page's «السجل» card has a third tab for it (health.js LOG_URLS.nightly).
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
HEALTH_PHP = DASH / "app" / "Http" / "Controllers" / "HealthController.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
BLADE = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
ROUTES = DASH / "routes" / "web.php"
PHP = shutil.which("php")
NODE = shutil.which("node")


def _payload(directory, date="", secrets=()):
    health = str(HEALTH_PHP).replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              f"require '{health}';\nuse App\\Http\\Controllers\\HealthController;\n"
              f"$out = HealthController::nightlyPayload({json.dumps(str(directory))}, {json.dumps(date)}, "
              f"json_decode({json.dumps(json.dumps(list(secrets)))}, true));\n"
              "echo json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def nightly_dir(tmp_path):
    folder = tmp_path / "temp" / "nightly"
    folder.mkdir(parents=True)
    (folder / "nightly_2026-10-01.log").write_text("night one\n", encoding="utf-8")
    (folder / "nightly_2026-10-02.log").write_text("night two\nkey=SECRET-SERPER-99 used\n", encoding="utf-8")
    (folder / "last_report.json").write_text("{}", encoding="utf-8")
    (folder / "notes.log").write_text("not a nightly log\n", encoding="utf-8")
    (tmp_path / "secret.log").write_text("outside the folder\n", encoding="utf-8")
    return folder


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_the_newest_night_is_shown_with_secrets_masked(nightly_dir):
    code, body = _payload(nightly_dir, secrets=["SECRET-SERPER-99"])
    assert code == 200 and body["exists"] is True and body["kind"] == "nightly"
    assert body["date"] == "2026-10-02" and body["dates"] == ["2026-10-02", "2026-10-01"]
    assert body["lines"] == ["night two", "key=[محجوب] used"]
    code, body = _payload(nightly_dir, "2026-10-01")
    assert code == 200 and body["lines"] == ["night one"]
    code, body = _payload(nightly_dir, "2026-09-01")                 # a night without a log: a normal state
    assert code == 200 and body["exists"] is False


@pytest.mark.skipif(PHP is None, reason="php is not installed")
@pytest.mark.parametrize("date", ["../secret", "..%2Fsecret", "2026-10-02.log", "2026-10-02/../../secret",
                                  "/etc/passwd", "C:\\Windows\\win.ini", "notes", "2026-1-2", "2026-10-02\x00"])
def test_anything_but_a_date_is_refused(nightly_dir, date):
    code, body = _payload(nightly_dir, date)
    assert code == 400 and body["status"] == "error" and "lines" not in body



NIGHTLY_LOG_HARNESS = r"""<?php
namespace App\Http\Controllers {
    abstract class Controller {}
    class SettingsController { public static function secretValues() { return []; } }
}
namespace Illuminate\Http {
    class Request {
        public $q;
        public function __construct($q) { $this->q = $q; }
        public function query($k, $d = null) { return array_key_exists($k, $this->q) ? $this->q[$k] : $d; }
    }
}
namespace {
    // like Laravel's HandleExceptions: a PHP warning ("Array to string conversion") becomes an exception (HTTP 500)
    set_error_handler(function ($no, $str) { throw new ErrorException($str, 0, $no); });
    class FakeResponse {
        public $code;
        public function __construct($body, $code) { $this->code = $code; }
        public function header($k, $v) { return $this; }
    }
    class FakeFactory { public function json($b, $c = 200, $h = [], $o = 0) { return new FakeResponse($b, $c); } }
    function response() { return new FakeFactory(); }
    function base_path($p = '') { return getenv('HARNESS_ROOT') . '/dashboard' . ($p !== '' ? '/' . $p : ''); }
    require getenv('HEALTH_CONTROLLER');
    $out = [];
    foreach ([['date' => ['x']], ['date' => ['2026-10-01']], ['date' => '2026-10-01'], []] as $q) {
        try {
            $out[] = (new App\Http\Controllers\HealthController())->nightlyLog(new Illuminate\Http\Request($q))->code;
        } catch (\Throwable $e) {
            $out[] = 500;
        }
    }
    echo json_encode($out);
}
"""


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_a_date_array_is_a_bad_request_not_a_server_error(nightly_dir, tmp_path):
    """Review fix P7: ?date[]=x reached (string) $request->query('date') as an array: "Array to string conversion",
    which Laravel turns into a 500. It is a 400 like any other date that is not a date."""
    (tmp_path / "dashboard").mkdir()
    script = tmp_path / "nightly_log.php"
    script.write_text(NIGHTLY_LOG_HARNESS, encoding="utf-8")
    env = dict(os.environ, HARNESS_ROOT=str(tmp_path), HEALTH_CONTROLLER=str(HEALTH_PHP))
    result = subprocess.run([PHP, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [400, 400, 200, 200]
    code, body = _payload(nightly_dir, ["2026-10-01"])
    assert code == 400 and "lines" not in body

@pytest.mark.skipif(PHP is None or not hasattr(os, "symlink"), reason="needs php and symlinks")
def test_a_symlink_out_of_the_folder_is_refused(nightly_dir, tmp_path):
    os.symlink(tmp_path / "secret.log", nightly_dir / "nightly_2026-10-03.log")
    code, body = _payload(nightly_dir, "2026-10-03")
    assert code == 400 and "outside the folder" not in json.dumps(body)
    # the newest-night default does not follow it either
    code, body = _payload(nightly_dir)
    assert code == 400 and "outside the folder" not in json.dumps(body)


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_no_nightly_run_yet_is_a_normal_state(tmp_path):
    code, body = _payload(tmp_path / "missing")
    assert code == 200 and body == {"status": "success", "kind": "nightly", "exists": False, "lines": [],
                                    "updated_at": None, "dates": [], "date": None}


def test_route_tab_and_script_url():
    routes = ROUTES.read_text(encoding="utf-8")
    assert "Route::get('/api/view-nightly-log', [\\App\\Http\\Controllers\\HealthController::class, 'nightlyLog']);" in routes
    blade = BLADE.read_text(encoding="utf-8")
    assert 'data-log-tab="nightly"' in blade and 'id="tab-nightly"' in blade
    script = HEALTH_JS.read_text(encoding="utf-8")
    assert "nightly: '/api/view-nightly-log'" in script
    controller = HEALTH_PHP.read_text(encoding="utf-8")
    body = controller[controller.index("public function nightlyLog("):controller.index("// Service cards")]
    assert "$request->query('date'" in body and "base_path('../temp/nightly')" in body
    assert "$request->query('file'" not in body and "$request->query('path'" not in body


@pytest.mark.skipif(PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                    reason="php or dashboard/vendor is not installed")
def test_the_route_answers_through_laravel(mariadb_or_skip, tmp_path):
    compiled = tmp_path / "views"
    compiled.mkdir()
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", APP_DEBUG="true",
               SESSION_DRIVER="array", CACHE_STORE="array", LOG_CHANNEL="stderr", VIEW_COMPILED_PATH=str(compiled),
               DB_CONNECTION="mariadb", DB_HOST=os.getenv("DB_HOST", "127.0.0.1"), DB_PORT=os.getenv("DB_PORT", "3306"),
               DB_DATABASE=os.environ["DB_DATABASE"], DB_USERNAME=os.getenv("DB_USERNAME", "root"),
               DB_PASSWORD=os.getenv("DB_PASSWORD", ""))
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (['/api/view-nightly-log', '/api/view-nightly-log?date=..%2F..%2F.env'] as $path) {{
    $response = $kernel->handle(Illuminate\\Http\\Request::create($path, 'GET'));
    $out[] = ['status' => $response->getStatusCode(), 'body' => json_decode($response->getContent(), true)];
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=env, capture_output=True, text=True, timeout=240,
                                encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    latest, traversal = json.loads(result.stdout)
    assert latest["status"] == 200 and latest["body"]["status"] == "success" and latest["body"]["kind"] == "nightly"
    assert traversal["status"] == 400 and "lines" not in traversal["body"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_nightly_tab_says_which_night_and_what_to_do_without_a_log():
    script = ("globalThis.window = globalThis;\n" + HEALTH_JS.read_text(encoding="utf-8") + "\n"
              "const H = window.LaqtaHealth; const now = new Date(2026, 9, 3, 9, 0).getTime();\n"
              "console.log(JSON.stringify({\n"
              " missing: H.logView({ status: 'success', exists: false, lines: [] }, 'nightly', now),\n"
              " ok: H.logView({ status: 'success', exists: true, lines: ['a'], date: '2026-10-03',"
              " updated_at: now / 1000 }, 'nightly', now)\n"
              "}));")
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["missing"]["text"].startswith("لسا ما في سجل.") and "schedule_nightly.ps1" in out["missing"]["text"]
    assert out["ok"]["meta"].startswith("ليلة 2026-10-03 · آخر سطر")
