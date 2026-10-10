"""Dashboard roles (users.role, ReviewerLimits, `php artisan laqta:user --role`), through the Laravel HTTP kernel with
the login cookie jar of test_dashboard_login and a stub cli_bridge:

* the migration gives every user that was there before it the admin role, and an admin still does everything;
* a reviewer approves, rejects, undoes a reject and dismisses a failed approval, but settings, runs, the Sheet and
  every other write answer 403 (JSON for the API, a page for a form) and never reach the bridge;
* a reviewer's pages carry no link to the admin pages;
* `laqta:user NAME --role=reviewer` makes a reviewer, `--role=admin` on an existing user changes only the role,
  `--list` shows the roles and an unknown role is refused.
"""

import json
import re
import subprocess

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, PHP, bridge_calls, sql, stub_env
from test_dashboard_login import artisan, run

pytestmark = NEEDS_LARAVEL

ADMIN, REVIEWER = "pytest-admin", "pytest-reviewer"
MIGRATION = "database/migrations/2026_10_10_000001_add_role_to_users.php"
DENIED = "هالعملية للمدير بس"


def migrate(env, direction="up"):
    """Run the role migration's up() or down() alone (the test database is built by local_cache_db, not artisan)."""
    code = (f"require 'vendor/autoload.php'; $app = require 'bootstrap/app.php'; "
            f"$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap(); (require '{MIGRATION}')->{direction}();")
    result = subprocess.run([PHP, "-r", code], cwd=DASH, env=env, capture_output=True, text=True, timeout=120,
                            encoding="utf-8")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def password_of(out):
    return re.search(r"كلمة السر: (\S+)", out).group(1)


def role_of(db, name):
    (row,) = sql(db, "SELECT role FROM users WHERE name = %s", (name,))
    return row["role"] if isinstance(row, dict) else row[0]


def login(name, password):
    return ["POST", "/login", {"username": name, "password": password}]


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
    environ, calls = stub_env(tmp_path, extra={
        "approval_enqueue": {"status": "queued", "job_id": 7},
        "reject_image": {"status": "success"},
        "undo_reject": {"status": "success"},
    })
    environ["LAQTA_TEST_LOGIN"] = "1"
    migrate(environ)
    yield environ, calls, db
    sql(db, "DELETE FROM users WHERE name LIKE 'pytest-%%'")


def test_users_from_before_the_roles_are_admins(env):
    environ, _calls, db = env
    migrate(environ, "down")                     # the table as production has it today
    try:
        password = password_of(artisan(environ, "laqta:user", ADMIN))
    finally:
        migrate(environ, "up")
    assert role_of(db, ADMIN) == "admin"
    _login, settings, sheet = run(environ, [login(ADMIN, password), ["GET", "/settings"],
                                            ["POST", "/api/sheet/save", {}]])
    assert settings["status"] == 200
    assert sheet["status"] != 403


def test_a_reviewer_reviews_but_admin_work_is_refused(env):
    environ, calls, _db = env
    password = password_of(artisan(environ, "laqta:user", REVIEWER, "--role=reviewer"))
    out = run(environ, [
        login(REVIEWER, password),
        ["POST", "/api/select_image", {"row_number": 2, "image_url": "https://cdn.example/a.png", "async": 1}],
        ["POST", "/api/reject_image", {"row_number": 2, "image_url": "https://cdn.example/b.png"}],
        ["POST", "/api/review/undo-reject", {"sku_key": "SKU-2", "row_number": 2, "image_url": "https://cdn.example/b.png"}],
        ["POST", "/api/approval-jobs/987654/dismiss"],
        ["GET", "/catalog"],
        ["POST", "/settings", {"serper_api_key": "x"}],
        ["GET", "/settings"],
        ["POST", "/api/run-all"],
        ["POST", "/api/stop-batch"],
        ["POST", "/api/sheet/save", {"spreadsheet_url": "https://docs.google.com/x", "tab_name": "Sheet1"}],
        ["POST", "/api/settings/bg-method", {"method": "none"}],
        ["POST", "/api/failures/retry", {"barcodes": ["1"]}],
        ["POST", "/api/run/brand-add", {"brand": "X"}],
        ["GET", "/batch-automation"],
        ["POST", "/logout"],
    ])
    approve, reject, undo, dismiss, catalog = out[1:6]
    assert approve["status"] == 202 and reject["status"] == 200 and undo["status"] == 200
    assert dismiss["status"] != 403 and catalog["status"] == 200
    form, page, *apis, run_page, logout = out[6:]
    assert form["status"] == 403 and page["status"] == 403 and DENIED in form["body"] and "<html" in form["body"]
    assert run_page["status"] == 403
    for res in apis:
        assert res["status"] == 403 and json.loads(res["body"]) == {"status": "error", "error": DENIED}
    assert logout["status"] == 302 and logout["location"].endswith("/login")
    actions = [action for action, _params in bridge_calls(calls)]
    assert actions[:3] == ["approval_enqueue", "reject_image", "undo_reject"]
    assert not {"run_all", "save_sheet_config", "save_settings", "retry_failures"} & set(actions)
    assert bridge_calls(calls)[0][1]["created_by"] == REVIEWER


def test_a_reviewer_sees_no_admin_links(env):
    environ, _calls, _db = env
    reviewer = password_of(artisan(environ, "laqta:user", REVIEWER, "--role=reviewer"))
    admin = password_of(artisan(environ, "laqta:user", ADMIN))
    _l1, as_reviewer, _l2, _l3, as_admin = run(environ, [
        login(REVIEWER, reviewer), ["GET", "/catalog"], ["POST", "/logout"], login(ADMIN, admin), ["GET", "/catalog"],
    ], body=400000)
    for page in (as_reviewer, as_admin):
        assert page["status"] == 200
    assert 'data-lq-role="reviewer"' in as_reviewer["body"]
    for link in ("/settings", "/batch-automation"):
        assert f'{link}"' not in as_reviewer["body"]
        assert f'{link}"' in as_admin["body"]
    # the review screen's config (data-config, HTML-escaped JSON): no «back to the queue» for a reviewer
    assert "&quot;retry&quot;:&quot;&quot;" in as_reviewer["body"] and "/api/failures/retry" in as_admin["body"]


def test_the_role_flags_of_laqta_user(env):
    environ, _calls, db = env
    out = artisan(environ, "laqta:user", REVIEWER, "--role=reviewer")
    assert "مراجع" in out
    password = password_of(out)
    password_of(artisan(environ, "laqta:user", ADMIN))
    listed = artisan(environ, "laqta:user", "--list")
    assert f"{ADMIN}  admin" in listed and f"{REVIEWER}  reviewer" in listed

    out = artisan(environ, "laqta:user", REVIEWER, "--role=admin")
    assert "صار" in out and "كلمة السر" not in out
    assert role_of(db, REVIEWER) == "admin"
    _login, settings = run(environ, [login(REVIEWER, password), ["GET", "/settings"]])    # same password, more rights
    assert settings["status"] == 200

    bad = subprocess.run([PHP, "artisan", "laqta:user", REVIEWER, "--role=owner"], cwd=DASH, env=environ,
                         capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert bad.returncode == 1
    assert role_of(db, REVIEWER) == "admin"
