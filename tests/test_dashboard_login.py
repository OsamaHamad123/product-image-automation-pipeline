"""Dashboard login (RequireLogin + LoginController + `php artisan laqta:user`), through the Laravel HTTP kernel with a
cookie jar between requests (the session cookie carries the login):

* every page and API needs a login: a page redirects to /login, an API answers 401 JSON; /healthz stays open;
* a wrong password is refused and the sixth try in a minute is locked out; the right one opens the dashboard;
* the owner's own machine (APP_ENV=local, request from 127.0.0.1) needs no login; any other address does;
* `laqta:user NAME` creates the user (or resets the password) and prints a password that logs in.
"""

import json
import os
import random
import re
import subprocess
import tempfile

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, PHP, sql, stub_env

pytestmark = NEEDS_LARAVEL

USER = "pytest-owner"


def run(env, requests_list):
    """[{status, location, body}] for [[method, uri, params, ip]], cookies kept between the requests. Each call is a
    new client address: the login lockout lives in Laravel's shared file cache and must not reach the next test."""
    client = f"203.0.113.{random.randint(1, 254)}"
    requests_list = [r if len(r) > 3 else [*r, *([{}] if len(r) < 3 else []), client] for r in requests_list]
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$jar = [];
$out = [];
foreach (json_decode(file_get_contents($argv[1]), true) as $r) {{
    $server = ['REMOTE_ADDR' => $r[3] ?? '203.0.113.7'];
    if (str_starts_with($r[1], '/api/')) {{ $server['HTTP_ACCEPT'] = 'application/json'; }}
    $request = Illuminate\\Http\\Request::create($r[1], $r[0], $r[2] ?? [], $jar, [], $server);
    $response = $kernel->handle($request);
    foreach ($response->headers->getCookies() as $c) {{ $jar[$c->getName()] = $c->getValue(); }}
    $out[] = ['status' => $response->getStatusCode(), 'location' => $response->headers->get('Location'),
              'body' => substr((string) $response->getContent(), 0, 4000)];
    $kernel->terminate($request, $response);
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.TemporaryDirectory() as folder:
        path, data = os.path.join(folder, "run.php"), os.path.join(folder, "spec.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(requests_list, fh)
        result = subprocess.run([PHP, path, data], cwd=DASH, env=env, capture_output=True, text=True, timeout=240,
                                encoding="utf-8")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def artisan(env, *args):
    result = subprocess.run([PHP, "artisan", "--no-ansi", *args], cwd=DASH, env=env, capture_output=True, text=True, timeout=120,
                            encoding="utf-8")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return result.stdout


@pytest.fixture
def env(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    sql(db, """CREATE TABLE IF NOT EXISTS users (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
        email_verified_at TIMESTAMP NULL, password VARCHAR(255) NOT NULL, remember_token VARCHAR(100) NULL,
        created_at TIMESTAMP NULL, updated_at TIMESTAMP NULL)""")
    sql(db, """CREATE TABLE IF NOT EXISTS sessions (
        id VARCHAR(255) PRIMARY KEY, user_id BIGINT UNSIGNED NULL, ip_address VARCHAR(45) NULL, user_agent TEXT NULL,
        payload LONGTEXT NOT NULL, last_activity INT NOT NULL)""")
    sql(db, "DELETE FROM users WHERE name LIKE 'pytest-%%'")
    environ, _ = stub_env(tmp_path)
    environ["LAQTA_TEST_LOGIN"] = "1"
    environ["CACHE_STORE"] = "file"          # the login rate limiter must outlive one request
    out = artisan(environ, "laqta:user", USER)
    password = re.search(r"كلمة السر: (\S+)", out).group(1)
    yield environ, password
    sql(db, "DELETE FROM users WHERE name LIKE 'pytest-%%'")


def test_pages_redirect_to_login_apis_answer_401_and_healthz_stays_open(env):
    environ, _ = env
    page, api, login, healthz = run(environ, [["GET", "/"], ["GET", "/api/overview"], ["GET", "/login"],
                                              ["GET", "/healthz"]])
    assert page["status"] == 302 and page["location"].endswith("/login")
    assert api["status"] == 401 and "سجّل دخول" in json.loads(api["body"])["error"]
    assert login["status"] == 200 and 'name="password"' in login["body"]
    assert healthz["status"] not in (302, 401)


def test_the_right_password_logs_in_and_logout_closes_the_session(env):
    environ, password = env
    login, back_to_login, logout, after = run(environ, [
        ["POST", "/login", {"username": USER, "password": password}],
        ["GET", "/login"],                       # already signed in: sent to the dashboard
        ["POST", "/logout"],
        ["GET", "/api/overview"],
    ])
    assert login["status"] == 302 and not login["location"].endswith("/login")
    assert back_to_login["status"] == 302 and not back_to_login["location"].endswith("/login")
    assert logout["status"] == 302 and logout["location"].endswith("/login")
    assert after["status"] == 401


def test_a_wrong_password_is_refused_and_the_sixth_try_is_locked_out(env):
    environ, password = env
    wrong = [["POST", "/login", {"username": USER, "password": "nope"}] for _ in range(5)]
    out = run(environ, wrong + [["POST", "/login", {"username": USER, "password": password}], ["GET", "/api/overview"]])
    assert all(r["status"] == 302 and r["location"].endswith("/login") for r in out[:6])
    assert out[6]["status"] == 401          # even the right password waits out the lock


def test_the_owners_machine_needs_no_login_other_addresses_do(env):
    environ, _ = env
    environ = dict(environ, APP_ENV="local")
    environ.pop("LAQTA_TEST_LOGIN")
    local, remote = run(environ, [["GET", "/api/system/attention", {}, "127.0.0.1"],
                                  ["GET", "/api/system/attention", {}, "198.51.100.4"]])
    assert local["status"] != 401
    assert remote["status"] == 401


def test_resetting_a_password_retires_the_old_one(env):
    environ, old = env
    out = artisan(environ, "laqta:user", USER)
    assert "تغيّرت كلمة سر" in out
    new = re.search(r"كلمة السر: (\S+)", out).group(1)
    assert new != old and len(new) == 20
    with_old, with_new = run(environ, [["POST", "/login", {"username": USER, "password": old}],
                                       ["POST", "/login", {"username": USER, "password": new}]])
    assert with_old["location"].endswith("/login")
    assert not with_new["location"].endswith("/login")


def test_user_names_are_checked():
    # no database needed: the name is refused before any query
    result = subprocess.run([PHP, "artisan", "laqta:user", "bad name!"], cwd=DASH, capture_output=True, text=True,
                            timeout=120, encoding="utf-8")
    assert result.returncode == 1
