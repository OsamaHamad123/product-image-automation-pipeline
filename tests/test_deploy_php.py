"""The dashboard side of the Ubuntu deployment kit, run under the PHP CLI:

- RunLauncher.php: the request file of laqta-run.path (a run started from the dashboard survives a php-fpm restart)
  and the pipeline.log rotation (the last 5 runs' logs are kept instead of deleted);
- ApiController::runAll: uses that launcher when the php-fpm pool says so, and the old nohup start otherwise;
- HealthzController.php: GET /healthz for an uptime monitor (the pure decision, the SQL against the real schema, and
  the real route through the Laravel kernel: JSON, 200/503, no cookie, no secret);
- HealthController::newestLaravelLog (the log viewer with LOG_STACK=daily);
- `artisan config:cache` / `route:cache` work (install.sh runs them) and a real environment variable beats a wrong
  .env line in the frozen configuration.

php and dashboard/vendor are needed (skipped with a clear reason otherwise); the /healthz tests use the real MariaDB
test database. Nothing reaches the network and no real worker is started: the nohup command is recorded, not run.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from laqta_kernel import stub_env

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"
PHP = shutil.which("php")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                                   reason="php or dashboard/vendor is missing")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux deployment kit")


def php_json(script, tmp_path, env=None, args=()):
    """Run a PHP script (text) and return what it printed, decoded from JSON."""
    path = tmp_path / "harness.php"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run([PHP, str(path), *args], capture_output=True, text=True, timeout=120, cwd=DASH,
                          env={**os.environ, **(env or {})})
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return json.loads(done.stdout)


# ---------------------------------------------------------------------------
# RunLauncher.php
# ---------------------------------------------------------------------------

LAUNCHER_HARNESS = r"""<?php
require getenv('LAUNCHER_PHP');
use App\Services\RunLauncher;
$dir = getenv('SCENARIO_DIR');
$names = function () use ($dir) { $n = array_values(array_diff(scandir($dir), ['.', '..'])); sort($n); return $n; };
$read = function ($f) use ($dir) { return is_file("$dir/$f") ? file_get_contents("$dir/$f") : null; };
$dump = function () use ($dir, $names, $read) { $o = []; foreach ($names() as $n) { $o[$n] = $read($n); } return $o; };
switch (getenv('SCENARIO')) {
    case 'mode':
        putenv('LAQTA_RUN_LAUNCHER=systemd'); $a = RunLauncher::mode();
        putenv('LAQTA_RUN_LAUNCHER=nohup');   $b = RunLauncher::mode();
        putenv('LAQTA_RUN_LAUNCHER');         $c = RunLauncher::mode();
        echo json_encode([$a, $b, $c, RunLauncher::mode('systemd'), RunLauncher::mode('SYSTEMD'), RunLauncher::mode('')]);
        break;
    case 'taken':
        $calls = 0; $seen = null;
        $ok = RunLauncher::requestRun($dir, 5.0, function ($s) use ($dir, &$calls, &$seen) {
            $calls++;
            $f = $dir . '/run_request.json';
            if ($calls === 2) { $seen = json_decode(file_get_contents($f), true); rename($f, $f . '.taken'); }
        });
        echo json_encode(['ok' => $ok, 'calls' => $calls, 'seen' => $seen, 'files' => $names()]);
        break;
    case 'nobody':
        $t = microtime(true);
        $ok = RunLauncher::requestRun($dir, 0.25, function ($s) { usleep(50000); });
        echo json_encode(['ok' => $ok, 'files' => $names(), 'took' => microtime(true) - $t]);
        break;
    case 'cancel_blocked':
        mkdir($dir . '/run_request.json.cancelled');
        $ok = RunLauncher::requestRun($dir, 0.1, function ($s) { usleep(20000); });
        echo json_encode(['ok' => $ok, 'files' => $names(), 'request' => is_file($dir . '/run_request.json')]);
        break;
    case 'unwritable':
        $t = microtime(true);
        echo json_encode(['ok' => RunLauncher::requestRun($dir . '/missing-folder', 5.0), 'took' => microtime(true) - $t]);
        break;
    case 'rotate':
        file_put_contents("$dir/pipeline.log", 'run-7');
        for ($i = 1; $i <= 5; $i++) { file_put_contents("$dir/pipeline.log.$i", "old-$i"); }
        echo json_encode(['r' => RunLauncher::rotateLog("$dir/pipeline.log"), 'files' => $dump()]);
        break;
    case 'rotate_keep_two':
        file_put_contents("$dir/pipeline.log", 'run-4');
        foreach ([1, 2, 3] as $i) { file_put_contents("$dir/pipeline.log.$i", "old-$i"); }
        echo json_encode(['r' => RunLauncher::rotateLog("$dir/pipeline.log", 2), 'files' => $dump()]);
        break;
    case 'rotate_empty_or_missing':
        $missing = RunLauncher::rotateLog("$dir/pipeline.log");
        file_put_contents("$dir/pipeline.log.1", 'precious');
        file_put_contents("$dir/pipeline.log", '');
        $empty = RunLauncher::rotateLog("$dir/pipeline.log");
        echo json_encode(['missing' => $missing, 'empty' => $empty, 'files' => $names(), 'one' => $read('pipeline.log.1')]);
        break;
    case 'seven_runs':
        for ($run = 1; $run <= 7; $run++) { RunLauncher::rotateLog("$dir/pipeline.log"); file_put_contents("$dir/pipeline.log", "run-$run"); }
        echo json_encode($dump());
        break;
}
"""


def run_launcher(tmp_path, scenario, env=None):
    folder = tmp_path / f"scenario_{scenario}"
    folder.mkdir()
    out = php_json(LAUNCHER_HARNESS, tmp_path, args=(), env={
        "LAUNCHER_PHP": str(DASH / "app" / "Services" / "RunLauncher.php"), "SCENARIO_DIR": str(folder),
        "SCENARIO": scenario, **(env or {})})
    return out, folder


@NEEDS_PHP
def test_launcher_mode_is_systemd_only_when_the_pool_says_so(tmp_path):
    modes, _ = run_launcher(tmp_path, "mode")
    assert modes == ["systemd", "nohup", "nohup", "systemd", "nohup", "nohup"]


@NEEDS_PHP
def test_launcher_writes_the_request_and_returns_true_once_the_unit_has_taken_it(tmp_path):
    out, folder = run_launcher(tmp_path, "taken")
    assert out["ok"] is True and out["calls"] == 2
    assert set(out["seen"]) == {"requested_at", "trigger"} and out["seen"]["trigger"] == "dashboard"
    assert out["files"] == ["run_request.json.taken"]               # no temp file and no request left over


@NEEDS_PHP
def test_launcher_withdraws_an_untaken_request_so_the_old_way_can_start_the_run(tmp_path):
    out, _ = run_launcher(tmp_path, "nobody")
    assert out["ok"] is False and 0.2 <= out["took"] < 3
    assert out["files"] == ["run_request.json.cancelled"]           # withdrawn by an atomic rename: no run can start twice


@NEEDS_PHP
def test_launcher_still_withdraws_when_the_atomic_rename_is_blocked(tmp_path):
    out, _ = run_launcher(tmp_path, "cancel_blocked")
    assert out["ok"] is False and out["request"] is False           # never leaves a request the unit could pick up later


@NEEDS_PHP
def test_launcher_gives_up_at_once_when_it_cannot_write_the_request(tmp_path):
    out, _ = run_launcher(tmp_path, "unwritable")
    assert out["ok"] is False and out["took"] < 1


@NEEDS_PHP
def test_pipeline_log_is_rotated_keeping_the_last_five(tmp_path):
    out, _ = run_launcher(tmp_path, "rotate")
    assert out["r"] is True
    assert out["files"] == {"pipeline.log.1": "run-7", "pipeline.log.2": "old-1", "pipeline.log.3": "old-2",
                            "pipeline.log.4": "old-3", "pipeline.log.5": "old-4"}   # old-5 dropped, no log left
    two, _ = run_launcher(tmp_path, "rotate_keep_two")
    assert two["files"] == {"pipeline.log.1": "run-4", "pipeline.log.2": "old-1"}


@NEEDS_PHP
def test_an_empty_or_missing_log_is_not_rotated(tmp_path):
    out, _ = run_launcher(tmp_path, "rotate_empty_or_missing")
    assert out["missing"] is True and out["empty"] is True
    assert out["files"] == ["pipeline.log", "pipeline.log.1"] and out["one"] == "precious"   # an empty run pushes nothing out


@NEEDS_PHP
def test_seven_runs_keep_exactly_the_five_newest_old_logs(tmp_path):
    out, _ = run_launcher(tmp_path, "seven_runs")
    assert out == {"pipeline.log": "run-7", "pipeline.log.1": "run-6", "pipeline.log.2": "run-5",
                   "pipeline.log.3": "run-4", "pipeline.log.4": "run-3", "pipeline.log.5": "run-2"}


# ---------------------------------------------------------------------------
# ApiController::runAll chooses the launcher
# ---------------------------------------------------------------------------

RUN_ALL_HARNESS = r"""<?php
namespace App\Services {
    class PythonBridge {
        public static function run($action, $params = []) { return ['status' => 'success']; }
        public static function pythonPath() { return '/venv/bin/python'; }
    }
}
namespace App\Http\Controllers {
    // runAll's unqualified shell_exec() resolves here first: the nohup command is recorded, never run
    function shell_exec($cmd) { $GLOBALS['SHELL'][] = $cmd; return null; }
}
namespace {
    $root = getenv('HARNESS_ROOT');
    function base_path($p = '') { return getenv('HARNESS_ROOT') . '/dashboard' . ($p !== '' ? '/' . $p : ''); }
    function response() {
        return new class { public function json($d, $s = 200) { return (object) ['data' => $d, 'status' => $s]; } };
    }
    $GLOBALS['SHELL'] = [];
    require getenv('DASH_DIR') . '/vendor/autoload.php';
    App\Services\RunLauncher::$waitSeconds = (float) getenv('LAUNCHER_WAIT');
    $temp = $root . '/temp';
    if (getenv('OLD_LOG') !== false) { file_put_contents($temp . '/pipeline.log', getenv('OLD_LOG')); }
    if (getenv('CONSUMER_DELAY') !== false) {
        // what laqta-run.service does: take the request with one atomic mv
        $req = $temp . '/run_request.json';
        exec('(sleep ' . getenv('CONSUMER_DELAY') . '; mv ' . escapeshellarg($req) . ' ' . escapeshellarg($req . '.taken')
            . ') > /dev/null 2>&1 &');
    }
    $res = (new App\Http\Controllers\ApiController())->runAll(Illuminate\Http\Request::create('/api/run-all', 'POST', ['brand' => 'x']));
    $names = array_values(array_diff(scandir($temp), ['.', '..'])); sort($names);
    echo json_encode(['status' => $res->status, 'data' => $res->data, 'shell' => $GLOBALS['SHELL'], 'files' => $names,
                      'lock' => @file_get_contents($temp . '/pipeline.lock'),
                      'log1' => @file_get_contents($temp . '/pipeline.log.1')]);
}
"""


def run_all(tmp_path, **env):
    root = tmp_path / "root"
    (root / "temp").mkdir(parents=True)
    (root / "dashboard").mkdir()
    return php_json(RUN_ALL_HARNESS, tmp_path, env={"HARNESS_ROOT": str(root), "DASH_DIR": str(DASH),
                                                    "LAUNCHER_WAIT": "0.3", "LAQTA_RUN_LAUNCHER": "", **env}), root


@NEEDS_LARAVEL
def test_run_all_hands_the_run_to_the_systemd_launcher_when_it_takes_the_request(tmp_path):
    out, root = run_all(tmp_path, LAQTA_RUN_LAUNCHER="systemd", LAUNCHER_WAIT="5", CONSUMER_DELAY="0.4",
                        OLD_LOG="yesterday's run")
    assert out["status"] == 200 and out["data"]["status"] == "success"
    assert out["shell"] == [], "php-fpm must not start the worker itself when the launcher took the request"
    assert out["lock"] == "STARTING"                                    # the stop button and the status page see the run
    assert "run_config.json" in out["files"] and "run_request.json.taken" in out["files"]
    assert out["log1"] == "yesterday's run" and "pipeline.log" not in out["files"]       # rotated, not deleted


@NEEDS_LARAVEL
def test_run_all_falls_back_to_nohup_when_nobody_takes_the_request_and_never_starts_twice(tmp_path):
    out, root = run_all(tmp_path, LAQTA_RUN_LAUNCHER="systemd", LAUNCHER_WAIT="0.3")
    assert out["status"] == 200 and len(out["shell"]) == 1
    command = out["shell"][0]
    assert command.startswith("nohup sh -c ") and "--enqueue" in command and "--worker --trigger=dashboard" in command
    assert "run_request.json" not in "".join(n for n in out["files"] if not n.endswith(".cancelled"))
    assert "run_request.json.cancelled" in out["files"]                # withdrawn: the unit can no longer take it


@NEEDS_LARAVEL
def test_run_all_keeps_the_old_start_when_the_launcher_is_not_installed(tmp_path):
    out, root = run_all(tmp_path)                                      # LAQTA_RUN_LAUNCHER empty (no php-fpm pool line)
    assert len(out["shell"]) == 1 and out["shell"][0].startswith("nohup sh -c ")
    assert not [n for n in out["files"] if n.startswith("run_request")]


# ---------------------------------------------------------------------------
# HealthzController
# ---------------------------------------------------------------------------

EVALUATE_HARNESS = r"""<?php
require getenv('DASH_DIR') . '/vendor/autoload.php';
use App\Http\Controllers\HealthzController as H;
$cases = json_decode($argv[1], true);
$out = [];
foreach ($cases as $facts) { $out[] = H::evaluate($facts, 1800000000); }
echo json_encode(['results' => $out, 'sql_nightly' => H::SQL_LAST_NIGHTLY, 'sql_dead' => H::SQL_DEAD]);
"""

GOOD = {"db": True, "nightly_known": True, "nightly_age_s": 5 * 3600, "dead_total": 0, "dead_recent": 0, "free_gb": 40.0}


def evaluate(tmp_path, *changes):
    cases = [{**GOOD, **c} for c in changes]
    out = php_json(EVALUATE_HARNESS, tmp_path, env={"DASH_DIR": str(DASH)}, args=(json.dumps(cases),))
    return out["results"], out


@NEEDS_LARAVEL
def test_healthz_is_200_when_everything_is_fine_and_says_so_in_plain_numbers(tmp_path):
    (code, body), = evaluate(tmp_path, {})[0]
    assert code == 200 and body["ok"] is True
    assert body["checks"] == {
        "database": {"ok": True},
        "nightly": {"ok": True, "status": "ok", "age_hours": 5.0, "max_hours": 26},
        "outbox": {"ok": True, "dead": 0, "dead_recent": 0, "window_days": 7},
        "disk": {"ok": True, "free_gb": 40.0, "min_gb": 2.0},
    }
    assert body["checked_at"] == "2027-01-15T08:00:00+00:00"


@NEEDS_LARAVEL
@pytest.mark.parametrize("change,failed", [
    ({"db": False}, "database"),
    ({"nightly_age_s": int(26.2 * 3600)}, "nightly"),                  # older than 26 hours
    ({"nightly_known": False, "nightly_age_s": None}, "nightly"),       # could not be read: never "fine"
    ({"dead_total": 3, "dead_recent": 1}, "outbox"),
    ({"dead_total": None, "dead_recent": None}, "outbox"),
    ({"free_gb": 1.9}, "disk"),
    ({"free_gb": None}, "disk"),
])
def test_healthz_is_503_naming_the_failed_check(tmp_path, change, failed):
    (code, body), = evaluate(tmp_path, change)[0]
    assert code == 503 and body["ok"] is False
    assert [name for name, check in body["checks"].items() if check["ok"] is not True] == [failed]


@NEEDS_LARAVEL
def test_healthz_edges_a_night_of_exactly_26_hours_never_ran_and_old_dead_rows(tmp_path):
    results, _ = evaluate(tmp_path, {"nightly_age_s": 26 * 3600}, {"nightly_known": True, "nightly_age_s": None},
                          {"dead_total": 5, "dead_recent": 0}, {"free_gb": 2.0})
    assert [code for code, _ in results] == [200, 200, 200, 200]
    assert results[1][1]["checks"]["nightly"] == {"ok": True, "status": "never", "age_hours": None, "max_hours": 26}
    assert results[2][1]["checks"]["outbox"]["dead"] == 5              # shown, but old ones do not keep it red for ever


@NEEDS_LARAVEL
def test_healthz_body_holds_no_path_name_or_error_text(tmp_path):
    (code, body), = evaluate(tmp_path, {"db": False, "nightly_age_s": 99999, "dead_recent": 2, "dead_total": 2})[0]
    text = json.dumps(body)
    for forbidden in ("/", "SQLSTATE", "password", "Exception", "secret", "key", "temp", "opt"):
        assert forbidden not in text.replace("2027-01-15T08:00:00+00:00", ""), forbidden


@pytest.fixture
def history(mariadb_or_skip):
    """run_history and sheet_updates of the test database, emptied; helpers to add a night and a dead write."""
    import google_sheets
    google_sheets.SQLiteTransactionQueue()
    db = mariadb_or_skip

    def sql(statement, params=()):
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(statement, params)
            rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    sql("DELETE FROM run_history")
    sql("DELETE FROM sheet_updates")

    def night(hours_ago, outcome="done", stop_reason=None, trigger="nightly", ended=True):
        sql("INSERT INTO run_history (run_trigger, started_at, ended_at, outcome, stop_reason) VALUES "
            "(%s, NOW() - INTERVAL %s MINUTE, " + ("NOW() - INTERVAL %s MINUTE" if ended else "%s") + ", %s, %s)",
            (trigger, int(hours_ago * 60), int(hours_ago * 60) - 120 if ended else None, outcome, stop_reason))

    def dead(n=1, days_ago=0, status="DEAD"):
        for _ in range(n):
            sql("INSERT INTO sheet_updates (`row_number`, col_index, `value`, sync_status, registered_at) "
                "VALUES (2, 1, 'v', %s, NOW() - INTERVAL %s DAY)", (status, days_ago))

    night.sql, night.dead = sql, dead
    return night


def healthz_sql(tmp_path):
    return evaluate(tmp_path, {})[1]


@NEEDS_LARAVEL
def test_healthz_nightly_sql_counts_only_nights_that_did_their_job(history, tmp_path):
    query = healthz_sql(tmp_path)["sql_nightly"]
    age = lambda: (history.sql(query) or [{"age_s": None}])[0]["age_s"]          # noqa: E731
    assert age() is None                                                            # no night at all
    for outcome, reason in (("failed", None), ("outage", "db_unavailable"), ("skipped", "another_worker"),
                            ("stopped", "stopped"), ("stopped", "serper_credit")):
        history(1, outcome, reason)
    history(1, "done", trigger="dashboard")                                         # a dashboard run is not the night
    assert age() is None, "none of these is a successful night"
    history(30, "done")
    assert 29.9 * 3600 < age() < 30.1 * 3600                                         # the age comes from the start time
    history(20, "stopped", "time_limit")                                             # ended by its own hour limit: fine
    assert 19.9 * 3600 < age() < 20.1 * 3600
    history(10, "handed_over")
    assert 9.9 * 3600 < age() < 10.1 * 3600
    history(5, "stopped", "budget_reached")
    assert 4.9 * 3600 < age() < 5.1 * 3600
    history(2, "done", ended=False)                                                  # a night still running: counted by start
    assert 1.9 * 3600 < age() < 2.1 * 3600


@NEEDS_LARAVEL
def test_healthz_dead_sql_separates_recent_from_old_and_ignores_other_statuses(history, tmp_path):
    query = healthz_sql(tmp_path)["sql_dead"]
    assert history.sql(query)[0] == {"total": 0, "recent": 0}
    history.dead(2, days_ago=1)
    history.dead(3, days_ago=30)
    history.dead(4, status="CONFLICT")
    history.dead(4, status="PENDING")
    row = history.sql(query)[0]
    assert (int(row["total"]), int(row["recent"])) == (5, 2)


def kernel_with_headers(env, paths):
    """[{status, body, set_cookie, cache_control}] for GET paths, through the real Laravel HTTP kernel."""
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as $uri) {{
    $request = Illuminate\\Http\\Request::create($uri, 'GET', [], [], [], ['HTTP_ACCEPT' => 'application/json']);
    $response = $kernel->handle($request);
    $out[] = ['status' => $response->getStatusCode(), 'body' => $response->getContent(),
              'set_cookie' => $response->headers->all('set-cookie'),
              'cache_control' => $response->headers->get('Cache-Control')];
    $kernel->terminate($request, $response);
}}
echo json_encode($out);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        done = subprocess.run([PHP, path, json.dumps(paths)], cwd=DASH, env=env, capture_output=True, text=True,
                              timeout=240)
    finally:
        os.unlink(path)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    return json.loads(done.stdout)


@NEEDS_LARAVEL
def test_healthz_route_answers_json_without_a_session_cookie_and_follows_the_database(history, tmp_path):
    env, _ = stub_env(tmp_path)
    secret = "-".join(("tst", "dbpw", "never", "printed", "7f3c9a1e"))
    env["DB_PASSWORD"] = env.get("DB_PASSWORD") or ""

    # a fresh server: no night yet is not a failure
    fresh, control = kernel_with_headers(env, ["/healthz", "/api/system/status"])
    body = json.loads(fresh["body"])
    assert fresh["status"] == 200 and body["ok"] is True and body["checks"]["nightly"]["status"] == "never"
    assert fresh["set_cookie"] == [], "a monitor polling every minute must not create a session"
    assert fresh["cache_control"] and "no-store" in fresh["cache_control"]
    assert control["set_cookie"], "control: a normal dashboard route does set the session cookie"

    history(6, "done")                                           # last night worked
    ok, = kernel_with_headers(env, ["/healthz"])
    assert ok["status"] == 200 and 5.9 <= json.loads(ok["body"])["checks"]["nightly"]["age_hours"] <= 6.2

    history.dead(1)                                              # one write never reached the sheet
    bad, = kernel_with_headers(env, ["/healthz"])
    parsed = json.loads(bad["body"])
    assert bad["status"] == 503 and parsed["ok"] is False and parsed["checks"]["outbox"]["dead_recent"] == 1
    assert secret not in bad["body"] and "SQLSTATE" not in bad["body"]


@NEEDS_LARAVEL
def test_healthz_answers_503_not_a_500_page_when_the_database_is_down(tmp_path):
    env, _ = stub_env(tmp_path)
    env["DB_HOST"], env["DB_PORT"] = "127.0.0.1", "1"           # nothing listens there
    down, = kernel_with_headers(env, ["/healthz"])
    body = json.loads(down["body"])
    assert down["status"] == 503 and body["checks"]["database"] == {"ok": False} and body["ok"] is False
    assert body["checks"]["nightly"]["status"] == "unknown"
    assert "refused" not in down["body"] and "SQLSTATE" not in down["body"] and "127.0.0.1" not in down["body"]


# ---------------------------------------------------------------------------
# HealthController::newestLaravelLog (the log viewer with LOG_STACK=daily)
# ---------------------------------------------------------------------------

NEWEST_HARNESS = r"""<?php
require getenv('DASH_DIR') . '/vendor/autoload.php';
$dir = $argv[1];
echo json_encode(basename(App\Http\Controllers\HealthController::newestLaravelLog($dir)));
"""


@NEEDS_LARAVEL
def test_log_viewer_shows_the_newest_of_laravel_log_and_the_daily_files(tmp_path):
    def newest():
        return php_json(NEWEST_HARNESS, tmp_path, env={"DASH_DIR": str(DASH)}, args=(str(tmp_path),))

    assert newest() == "laravel.log"                              # nothing there: the default name ("not found" page)
    now = time.time()

    def touch(name, age_s):
        path = tmp_path / name
        path.write_text("x")
        os.utime(path, (now - age_s, now - age_s))

    touch("laravel.log", 300)
    assert newest() == "laravel.log"
    touch("laravel-2026-10-05.log", 200)
    touch("laravel-2026-10-06.log", 100)
    assert newest() == "laravel-2026-10-06.log"                   # LOG_STACK=daily: today's file
    touch("laravel.log", 10)                                       # python wrote an error just now
    assert newest() == "laravel.log"
    touch("laravel-notes.txt", 1)                                  # not a log of Laravel
    assert newest() == "laravel.log"


# ---------------------------------------------------------------------------
# artisan config:cache / route:cache (install.sh runs both)
# ---------------------------------------------------------------------------

def artisan(tmp_path, *args, extra=None):
    env = {**os.environ, "APP_ENV": "production", "APP_KEY": "base64:" + "A" * 43 + "=",
           "APP_CONFIG_CACHE": str(tmp_path / "config.php"), "APP_ROUTES_CACHE": str(tmp_path / "routes.php"),
           "APP_SERVICES_CACHE": str(tmp_path / "services.php"), "APP_PACKAGES_CACHE": str(tmp_path / "packages.php"),
           **(extra or {})}
    return subprocess.run([PHP, "artisan", *args], cwd=DASH, env=env, capture_output=True, text=True, timeout=240)


@NEEDS_LARAVEL
def test_config_cache_and_route_cache_succeed(tmp_path):
    """A Closure left in a config file (the sqlite `after` hook was one) makes config:cache fail and install.sh stop."""
    done = artisan(tmp_path, "config:cache")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "config.php").is_file()
    done = artisan(tmp_path, "route:cache")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "healthz" in (tmp_path / "routes.php").read_text(encoding="utf-8")


@NEEDS_LARAVEL
def test_a_real_environment_variable_beats_a_wrong_env_line_in_the_frozen_config(tmp_path):
    """install.sh runs config:cache with APP_DEBUG=false APP_ENV=production (SESSION_SECURE_COOKIE=true on HTTPS)."""
    env_file = DASH / ".env.production"                           # APP_ENV=production makes Laravel read this file
    if env_file.exists():
        pytest.skip("dashboard/.env.production exists on this machine; not touching it")
    try:
        env_file.write_text("APP_DEBUG=true\nSESSION_SECURE_COOKIE=false\n", encoding="utf-8")
        plain = artisan(tmp_path, "config:cache")
        assert plain.returncode == 0, plain.stderr
        text = (tmp_path / "config.php").read_text(encoding="utf-8")
        assert "'debug' => true," in text and "'secure' => false," in text     # the file alone: unsafe

        baked = artisan(tmp_path, "config:cache", extra={"APP_DEBUG": "false", "SESSION_SECURE_COOKIE": "true"})
        assert baked.returncode == 0, baked.stderr
        text = (tmp_path / "config.php").read_text(encoding="utf-8")
        assert "'debug' => false," in text and "'secure' => true," in text
    finally:
        env_file.unlink(missing_ok=True)
