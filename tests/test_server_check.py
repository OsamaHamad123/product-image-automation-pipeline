"""scripts/server_check.py with everything mocked: no network, no database, no systemd, no real sheet."""

import importlib.util
import json
import os
import sys
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("server_check_under_test", REPO / "scripts" / "server_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = _load()

def _fake(tag):
    """A throw-away value built at run time: the source never holds a "name = literal" credential pair."""
    return "-".join(("tst", tag, "never", "printed", "7f3c9a1e"))


# names assembled, values built: GitGuardian scans every commit of a pull request
SECRETS = {name: _fake(name.lower()[:6] + str(i)) for i, name in enumerate((
    "SERPER_API_" + "KEY", "GEMINI_API_" + "KEY", "CLOUDINARY_API_" + "KEY", "CLOUDINARY_API_" + "SECRET",
    "DB_PASS" + "WORD", "PHOTOROOM_API_" + "KEY"))}
GOOD_ENV = {
    "DB_HOST": "127.0.0.1", "DB_PORT": "3306", "DB_DATABASE": "automation_db", "DB_USERNAME": "laqta_app",
    "SPREADSHEET_NAME_OR_URL": "https://docs.google.com/spreadsheets/d/abc", "CLOUDINARY_CLOUD_NAME": "mycloud",
    "BG_REMOVAL_METHOD": "photoroom", **SECRETS,
}


def write_env(path, values, extra=""):
    path.write_text("# comment\n" + "".join(f'{k}="{v}"\n' for k, v in values.items()) + extra, encoding="utf-8")
    path.chmod(0o600)


def make_root(tmp_path, env=None, dash=None, creds=True, venv=True):
    """A fake repository folder with .env, .env.example, dashboard/.env, credentials.json and a venv."""
    (tmp_path / "dashboard" / "bootstrap" / "cache").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "local_cache_db.py").write_text(
        'cur.execute("CREATE TABLE IF NOT EXISTS resolved_products (id INT)")\n'
        'cur.execute("CREATE TABLE IF NOT EXISTS system_settings (k INT)")\n'
        'cur.execute("CREATE TABLE IF NOT EXISTS automation_queue (id INT)")\n', encoding="utf-8")
    (tmp_path / ".env.example").write_text("SERPER_API_KEY=\nGEMINI_API_KEY=your-gemini-api-key\nPROXY_URL=\n"
                                           "DB_HOST=127.0.0.1\n# COMMENTED=1\n", encoding="utf-8")
    write_env(tmp_path / ".env", GOOD_ENV if env is None else env)
    write_env(tmp_path / "dashboard" / ".env", dash if dash is not None else {
        "APP_KEY": "base64:abcdefghijklmnopqrstuvwxyz0123456789ABCDEF=", "APP_ENV": "production", "APP_DEBUG": "false",
        "DB_HOST": "127.0.0.1", "DB_PORT": "3306", "DB_DATABASE": "automation_db", "DB_USERNAME": "laqta_app",
        "DB_PASSWORD": SECRETS["DB_PASSWORD"]})
    if creds:
        (tmp_path / "credentials.json").write_text(json.dumps(
            {"type": "service_account", "client_email": "bot@x.iam.gserviceaccount.com", "private_key": "k"}))
        (tmp_path / "credentials.json").chmod(0o600)
    if venv:
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python").write_text("")
    return tmp_path


def statuses(results):
    return [r.status for r in results]


def texts(results):
    return "\n".join(r.text for r in results)


# ------------------------------------------------------------------ python and venv
def test_python_version_and_venv(tmp_path):
    root = make_root(tmp_path)
    assert statuses(sc.check_python((3, 12, 3), str(root / ".venv"), str(root))) == [sc.OK, sc.OK]
    assert sc.check_python((3, 9, 18), "/usr", str(root))[0].status == sc.FAIL
    outside = sc.check_python((3, 12, 3), "/usr", str(root))
    assert outside[1].status == sc.WARN and ".venv" in outside[1].text


def test_missing_venv_is_a_failure(tmp_path):
    root = make_root(tmp_path, venv=False)
    out = sc.check_python((3, 12, 3), "/usr", str(root))
    assert out[1].status == sc.FAIL


# ------------------------------------------------------------------ .env keys: names only
def test_read_env_file_matches_config_loader(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# c\n\nA=1\nB = \"two words\"\nC='x'\nBAD LINE\nEMPTY=\n", encoding="utf-8")
    assert sc.read_env_file(str(path)) == {"A": "1", "B": "two words", "C": "x", "EMPTY": ""}
    assert sc.read_env_file(str(tmp_path / "missing")) is None


def test_env_check_passes_and_never_prints_a_value(tmp_path):
    root = make_root(tmp_path)
    out = sc.check_env(str(root), environ={})
    assert sc.FAIL not in statuses(out), texts(out)
    shown = texts(out)
    for value in list(SECRETS.values()) + ["base64:abcdefghijklmnopqrstuvwxyz0123456789ABCDEF=", "mycloud", "laqta_app"]:
        assert value not in shown


def test_env_check_names_the_missing_and_placeholder_keys(tmp_path):
    env = {k: v for k, v in GOOD_ENV.items() if k not in ("SERPER_API_KEY", "CLOUDINARY_API_SECRET")}
    env["GEMINI_API_KEY"] = "your-gemini-api-key"
    root = make_root(tmp_path, env=env)
    out = sc.check_env(str(root), environ={})
    fail = next(r for r in out if r.status == sc.FAIL and "مطلوبة" in r.text)
    for name in ("SERPER_API_KEY", "CLOUDINARY_API_SECRET", "GEMINI_API_KEY"):
        assert name in fail.text
    assert "CLOUDINARY_API_KEY" not in fail.text
    assert "your-gemini-api-key" not in texts(out)
    warn = next(r for r in out if r.status == sc.WARN and "من .env.example" in r.text)
    assert "PROXY_URL" in warn.text and "COMMENTED" not in warn.text


def test_dashboard_settings_page_values_count_as_present(tmp_path):
    env = {k: v for k, v in GOOD_ENV.items() if k != "GEMINI_API_KEY"}
    root = make_root(tmp_path, env=env)
    assert any(r.status == sc.FAIL for r in sc.check_env(str(root), environ={}))
    out = sc.check_env(str(root), environ={}, settings={"gemini_api_key": "from-the-settings-page"})
    assert sc.FAIL not in statuses(out), texts(out)


def test_a_real_environment_variable_counts_as_a_key(tmp_path):
    env = {k: v for k, v in GOOD_ENV.items() if k != "SERPER_API_KEY"}
    root = make_root(tmp_path, env=env)
    assert sc.FAIL not in statuses(sc.check_env(str(root), environ={"SERPER_API_KEY": "exported-in-the-unit"}))


def test_dashboard_env_problems(tmp_path):
    base = {"APP_KEY": "base64:abcdefghij", "APP_ENV": "production", "APP_DEBUG": "false", "DB_HOST": "127.0.0.1",
            "DB_PORT": "3306", "DB_DATABASE": "automation_db", "DB_USERNAME": "laqta_app",
            "DB_PASSWORD": SECRETS["DB_PASSWORD"]}
    empty_key = sc.check_env(str(make_root(tmp_path / "a", dash={**base, "APP_KEY": ""})), environ={})
    assert any(r.status == sc.FAIL and "APP_KEY" in r.text for r in empty_key)
    debug = sc.check_env(str(make_root(tmp_path / "b", dash={**base, "APP_DEBUG": "true"})), environ={})
    assert any(r.status == sc.FAIL and "APP_DEBUG" in r.text for r in debug)
    local = sc.check_env(str(make_root(tmp_path / "c", dash={**base, "APP_ENV": "local"})), environ={})
    assert any(r.status == sc.WARN and "APP_ENV" in r.text for r in local)
    other_db = sc.check_env(str(make_root(tmp_path / "d", dash={**base, "DB_DATABASE": "elsewhere"})), environ={})
    warn = next(r for r in other_db if r.status == sc.WARN and "dashboard/.env" in r.text)
    assert "DB_DATABASE" in warn.text and "elsewhere" not in warn.text


def test_missing_env_files_are_failures(tmp_path):
    root = make_root(tmp_path, creds=False)
    (root / ".env").unlink()
    (root / "dashboard" / ".env").unlink()
    out = sc.check_env(str(root), environ={})
    assert [r.status for r in out].count(sc.FAIL) == 3


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_secret_files_readable_by_others_are_flagged(tmp_path):
    root = make_root(tmp_path)
    (root / ".env").chmod(0o644)
    out = sc.check_env(str(root), environ={})
    warn = next(r for r in out if r.status == sc.WARN and "chmod 600" in r.text)
    assert ".env" in warn.text and "credentials.json" not in warn.text


def test_stale_config_cache_is_flagged(tmp_path):
    root = make_root(tmp_path)
    cache = root / "dashboard" / "bootstrap" / "cache" / "config.php"
    cache.write_text("<?php return [];")
    os.utime(cache, (1_000_000_000, 1_000_000_000))
    assert any("config:cache" in r.text for r in sc.check_env(str(root), environ={}) if r.status == sc.WARN)
    os.utime(cache, None)
    assert not any("config:cache" in r.text for r in sc.check_env(str(root), environ={}))


def test_credentials_file_shapes(tmp_path):
    path = tmp_path / "c.json"
    assert sc.check_credentials(str(path))[0].status == sc.FAIL
    path.write_text("not json")
    assert sc.check_credentials(str(path))[0].status == sc.FAIL
    path.write_text(json.dumps({"type": "authorized_user"}))
    assert sc.check_credentials(str(path))[0].status == sc.FAIL
    path.write_text(json.dumps({"type": "service_account", "client_email": "a@b", "private_key": "k"}))
    out = sc.check_credentials(str(path))
    assert out[0].status == sc.OK and "a@b" not in out[0].text


# ------------------------------------------------------------------ database
class FakeCursor:
    def __init__(self, tables, settings):
        self.tables, self.settings, self.sql = tables, settings, ""

    def execute(self, sql, *args):
        self.sql = sql

    def fetchall(self):
        if self.sql.startswith("SHOW TABLES"):
            return [(t,) for t in self.tables]
        return list(self.settings.items())


class FakeConn:
    def __init__(self, tables, settings):
        self.cur, self.closed = FakeCursor(tables, settings), False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_expected_tables_come_from_the_real_schema_source():
    tables = sc.expected_tables(str(REPO))
    for name in ("automation_queue", "system_settings", "resolved_products", "run_history", "search_spend"):
        assert name in tables


def test_db_ok_returns_settings_and_closes(tmp_path):
    root = make_root(tmp_path)
    conn = FakeConn(["resolved_products", "system_settings", "automation_queue"], {"bg_removal_method": "rembg"})
    seen = {}

    def connect(**kwargs):
        seen.update(kwargs)
        return conn

    results, settings = sc.check_db(GOOD_ENV, root=str(root), connect=connect)
    assert statuses(results) == [sc.OK] and "3 جدول" in results[0].text
    assert settings == {"bg_removal_method": "rembg"} and conn.closed
    assert seen["host"] == "127.0.0.1" and seen["database"] == "automation_db" and seen["connect_timeout"] == 5


def test_db_missing_tables_are_named(tmp_path):
    root = make_root(tmp_path)
    results, _ = sc.check_db(GOOD_ENV, root=str(root), connect=lambda **k: FakeConn(["system_settings"], {}))
    assert results[0].status == sc.FAIL
    assert "resolved_products" in results[0].text and "automation_queue" in results[0].text
    assert "system_settings" not in results[0].text.split("ناقصها جداول:")[1].split("(")[0]


def test_db_connection_error_never_shows_the_password(tmp_path):
    root = make_root(tmp_path)

    def connect(**kwargs):
        raise RuntimeError(1045, "Access denied (using password: %s)" % kwargs["password"])

    results, settings = sc.check_db(GOOD_ENV, root=str(root), connect=connect)
    assert results[0].status == sc.FAIL and "1045" in results[0].text
    assert SECRETS["DB_PASSWORD"] not in results[0].text and settings == {}


def test_db_query_error_is_a_failure(tmp_path):
    class Boom(FakeConn):
        def cursor(self):
            raise OSError("lost connection")

    root = make_root(tmp_path)
    conn = Boom([], {})
    results, _ = sc.check_db(GOOD_ENV, root=str(root), connect=lambda **k: conn)
    assert results[0].status == sc.FAIL and conn.closed


# ------------------------------------------------------------------ sheet
def test_sheet_check_outcomes():
    assert sc.check_sheet({}, skip=True)[0].status == sc.INFO
    assert sc.check_sheet({}, open_sheet=lambda: (object(), object()))[0].status == sc.OK

    def no_client():
        raise SystemExit("No Google Sheets client: check CREDENTIALS_FILE in .env")

    assert sc.check_sheet({}, open_sheet=no_client)[0].status == sc.FAIL

    def forbidden():
        raise RuntimeError("APIError 403 for key=%s on sheet" % SECRETS["GEMINI_API_KEY"])

    out = sc.check_sheet({"GEMINI_API_KEY": SECRETS["GEMINI_API_KEY"]}, open_sheet=forbidden)[0]
    assert out.status == sc.FAIL and "403" in out.text and SECRETS["GEMINI_API_KEY"] not in out.text


def test_sheet_check_swallows_import_time_prints(capsys):
    def noisy():
        print("config says hello with a key AIzaSyFAKE")
        return object(), object()

    assert sc.check_sheet({}, open_sheet=noisy)[0].status == sc.OK
    assert "hello" not in capsys.readouterr().out


# ------------------------------------------------------------------ cloudinary and background removal
def test_cloudinary_configured_or_not():
    assert sc.check_cloudinary(GOOD_ENV)[0].status == sc.OK
    partial = {k: v for k, v in GOOD_ENV.items() if k != "CLOUDINARY_API_SECRET"}
    out = sc.check_cloudinary(partial)[0]
    assert out.status == sc.FAIL and "CLOUDINARY_API_SECRET" in out.text and "CLOUDINARY_API_KEY" not in out.text
    placeholder = {**GOOD_ENV, "CLOUDINARY_CLOUD_NAME": "your-cloud-name"}
    assert sc.check_cloudinary(placeholder)[0].status == sc.FAIL
    assert sc.check_cloudinary(placeholder, {"cloudinary_cloud_name": "from-settings"})[0].status == sc.OK


def bg(env, settings=None, installed=(), files=(), environ=None):
    return sc.check_bg_removal(
        env, settings, find_spec=lambda name: object() if name in installed else None,
        environ=environ if environ is not None else {}, listdir=lambda folder: list(files))


def test_bg_photoroom_needs_its_key():
    ok = bg(GOOD_ENV)
    assert ok[0].text.endswith("photoroom") and ok[1].status == sc.OK
    no_key = bg({k: v for k, v in GOOD_ENV.items() if k != "PHOTOROOM_API_KEY"})
    assert no_key[1].status == sc.FAIL and "PHOTOROOM_API_KEY" in no_key[1].text


def test_bg_rembg_needs_the_package_and_a_model_file():
    env = {**GOOD_ENV, "BG_REMOVAL_METHOD": "rembg", "U2NET_HOME": "/var/lib/laqta/models"}
    not_installed = bg(env)
    assert not_installed[1].status == sc.FAIL and "--with-birefnet" in not_installed[1].text
    only_rembg = bg(env, installed=("rembg", "onnxruntime"))
    assert only_rembg[1].status == sc.FAIL and "/var/lib/laqta/models" in only_rembg[1].text
    half = bg(env, installed=("rembg",))                      # onnxruntime missing too
    assert half[1].status == sc.FAIL
    ready = bg(env, installed=("rembg", "onnxruntime"), files=["birefnet-general.onnx", "notes.txt"])
    assert ready[1].status == sc.OK and "birefnet-general.onnx" in ready[1].text and "notes.txt" not in ready[1].text


def test_bg_named_model_must_be_the_one_present():
    env = {**GOOD_ENV, "BG_REMOVAL_METHOD": "rembg", "REMBG_MODEL": "birefnet-general"}
    wrong = bg(env, installed=("rembg", "onnxruntime"), files=["birefnet-general-lite.onnx"])
    assert wrong[1].status == sc.FAIL
    right = bg(env, installed=("rembg", "onnxruntime"), files=["birefnet-general.onnx"])
    assert right[1].status == sc.OK


def test_bg_settings_page_method_wins_over_env():
    out = bg({**GOOD_ENV, "BG_REMOVAL_METHOD": "photoroom"}, {"bg_removal_method": "grabcut"})
    assert out[0].text.endswith("grabcut") and out[1].status == sc.OK


def test_bg_unknown_method_and_rembg_present_but_unused():
    assert bg({**GOOD_ENV, "BG_REMOVAL_METHOD": "magic"})[1].status == sc.WARN
    unused = bg(GOOD_ENV, installed=("rembg", "onnxruntime"), files=["birefnet-general.onnx"])
    assert unused[-1].status == sc.INFO and "مو مستعمل" in unused[-1].text


# ------------------------------------------------------------------ disk
Usage = namedtuple("Usage", "total used free")


def test_disk_thresholds():
    gb = 1024 ** 3
    assert sc.check_disk("/x", disk_usage=lambda p: Usage(100 * gb, 90 * gb, 10 * gb))[0].status == sc.OK
    assert sc.check_disk("/x", disk_usage=lambda p: Usage(100 * gb, 96 * gb, 4 * gb))[0].status == sc.WARN
    assert sc.check_disk("/x", disk_usage=lambda p: Usage(100 * gb, 99 * gb, 1 * gb))[0].status == sc.FAIL
    assert sc.check_disk("/x", min_free_gb=0.5, disk_usage=lambda p: Usage(100 * gb, 99 * gb, 1 * gb))[0].status == sc.WARN

    def broken(path):
        raise FileNotFoundError(path)

    assert sc.check_disk("/x", disk_usage=broken)[0].status == sc.FAIL


# ------------------------------------------------------------------ systemd units and nginx
def fake_systemctl(active=(), enabled=(), fpm="php8.3-fpm.service"):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        action, args = cmd[1], cmd[2:]
        if action == "is-active":
            out = "active" if args[0] in active else "inactive"
        elif action == "is-enabled":
            out = "enabled" if args[0] in enabled else "disabled"
        elif action == "list-unit-files":
            out = f"{fpm} enabled enabled\n" if fpm else ""
        elif action == "show":
            out = "Tue 2026-10-06 02:00:00 UTC"
        else:
            out = ""
        return SimpleNamespace(stdout=out + "\n", returncode=0)

    run.calls = calls
    return run


ALL_ACTIVE = ("laqta-nightly.timer", "laqta-backup.timer", "laqta-outbox-flush.timer", "nginx.service",
              "mariadb.service", "php8.3-fpm.service")


def test_units_all_running():
    fake = fake_systemctl(active=ALL_ACTIVE)
    out = sc.check_units(run=fake)
    assert sc.FAIL not in statuses(out), texts(out)
    assert any("الجولة الجاية" in r.text for r in out)
    assert any(r.status == sc.INFO and "Redis" in r.text for r in out)          # worker disabled = fine
    assert {c[1] for c in fake.calls} <= {"is-active", "is-enabled", "show", "list-unit-files"}   # read-only calls


def test_units_stopped_timer_and_service_fail():
    out = sc.check_units(run=fake_systemctl(active=("nginx.service", "php8.3-fpm.service")))
    failed = [r.text for r in out if r.status == sc.FAIL]
    assert any("laqta-nightly.timer" in t for t in failed) and any("laqta-backup.timer" in t for t in failed)
    assert any("mariadb.service" in t for t in failed)


def test_enabled_sync_worker_must_be_running_and_no_fpm_is_a_failure():
    stopped = sc.check_units(run=fake_systemctl(active=ALL_ACTIVE, enabled=("laqta-sync-worker.service",)))
    assert any(r.status == sc.FAIL and "laqta-sync-worker" in r.text for r in stopped)
    running = sc.check_units(run=fake_systemctl(active=ALL_ACTIVE + ("laqta-sync-worker.service",),
                                                enabled=("laqta-sync-worker.service",)))
    assert sc.FAIL not in statuses(running)
    no_fpm = sc.check_units(run=fake_systemctl(active=ALL_ACTIVE, fpm=""))
    assert any(r.status == sc.FAIL and "php-fpm" in r.text for r in no_fpm)


def test_systemd_detection():
    assert sc.systemd_available(which=lambda n: "/bin/systemctl", exists=lambda p: True)
    assert not sc.systemd_available(which=lambda n: None, exists=lambda p: True)
    assert not sc.systemd_available(which=lambda n: "/bin/systemctl", exists=lambda p: False)


SITE = """server {
    listen 80;
    auth_basic "Laqta";
    auth_basic_user_file /etc/nginx/laqta.htpasswd;
    location ^~ /.well-known/acme-challenge/ { auth_basic off; }
}
"""


def nginx_dir(tmp_path, site=SITE, users=True):
    (tmp_path / "sites-enabled").mkdir()
    if site is not None:
        (tmp_path / "sites-enabled" / "laqta.conf").write_text(site)
    if users:
        (tmp_path / "laqta.htpasswd").write_text("admin:$2y$05$hash\n")
    return str(tmp_path)


def test_web_check_requires_auth_and_warns_on_plain_http(tmp_path):
    out = sc.check_web(nginx_dir(tmp_path))
    assert out[0].status == sc.OK and out[1].status == sc.WARN and "HTTP" in out[1].text


def test_web_check_https_and_local_only_are_fine(tmp_path):
    https = SITE.replace("listen 80;", "listen 443 ssl; # managed by Certbot\n    ssl_certificate /x.pem;")
    (tmp_path / "a").mkdir()
    assert statuses(sc.check_web(nginx_dir(tmp_path / "a", https))) == [sc.OK, sc.OK]
    (tmp_path / "b").mkdir()
    local = SITE.replace("listen 80;", "listen 127.0.0.1:8080;")
    assert statuses(sc.check_web(nginx_dir(tmp_path / "b", local))) == [sc.OK, sc.OK]


def test_web_check_fails_without_basic_auth_or_users(tmp_path):
    (tmp_path / "a").mkdir()
    no_auth = sc.check_web(nginx_dir(tmp_path / "a", SITE.replace('auth_basic "Laqta";', "")))
    assert no_auth[0].status == sc.FAIL and "كلمة سر" in no_auth[0].text
    (tmp_path / "b").mkdir()
    off = sc.check_web(nginx_dir(tmp_path / "b", SITE.replace('auth_basic "Laqta";', "auth_basic off;"), users=True))
    assert off[0].status == sc.FAIL
    (tmp_path / "c").mkdir()
    no_users = sc.check_web(nginx_dir(tmp_path / "c", users=False))
    assert no_users[0].status == sc.FAIL
    (tmp_path / "d").mkdir()
    assert sc.check_web(nginx_dir(tmp_path / "d", site=None))[0].status == sc.FAIL


def test_outbox_flush_timer_is_required():
    out = sc.check_units(run=fake_systemctl(active=tuple(u for u in ALL_ACTIVE if u != "laqta-outbox-flush.timer")))
    failed = [r.text for r in out if r.status == sc.FAIL]
    assert len(failed) == 1 and "laqta-outbox-flush.timer" in failed[0]


def test_run_launcher_is_ok_warned_or_just_mentioned():
    on = sc.check_units(run=fake_systemctl(active=ALL_ACTIVE + ("laqta-run.path",), enabled=("laqta-run.path",)))
    assert sc.FAIL not in statuses(on) and sc.WARN not in statuses(on)
    assert any(r.status == sc.OK and "laqta-run.path" in r.text for r in on)
    stopped = sc.check_units(run=fake_systemctl(active=ALL_ACTIVE, enabled=("laqta-run.path",)))
    warn = [r for r in stopped if r.status == sc.WARN]
    assert len(warn) == 1 and "laqta-run.path" in warn[0].text and sc.FAIL not in statuses(stopped)   # runs still work
    absent = sc.check_units(run=fake_systemctl(active=ALL_ACTIVE))
    assert any(r.status == sc.INFO and "laqta-run.path" in r.text for r in absent) and sc.WARN not in statuses(absent)


def write_cached_config(root, env="production", debug="false", secure="NULL"):
    """The lines `artisan config:cache` writes (checked against a real cache file of this dashboard)."""
    cache = Path(root) / "dashboard" / "bootstrap" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "config.php").write_text(
        "<?php return array (\n  'app' => \n  array (\n    'name' => 'Laqta',\n    'env' => '%s',\n"
        "    'debug' => %s,\n    'url' => 'http://localhost',\n  ),\n  'session' => \n  array (\n"
        "    'driver' => 'file',\n    'secure' => %s,\n    'same_site' => 'lax',\n  ),\n);\n" % (env, debug, secure))


def test_frozen_config_with_debug_on_is_a_failure_even_when_env_says_false(tmp_path):
    root = make_root(tmp_path)                                       # dashboard/.env: APP_DEBUG=false
    write_cached_config(root, debug="true")
    out = sc.check_env(str(root), environ={})
    assert any(r.status == sc.FAIL and "APP_DEBUG" in r.text and "config:cache" in r.text for r in out)
    write_cached_config(root, debug="false")
    assert not any(r.status == sc.FAIL and "APP_DEBUG" in r.text for r in sc.check_env(str(root), environ={}))


def test_app_env_is_judged_by_what_the_installer_froze(tmp_path):
    """install.sh bakes APP_ENV=production into the cached config even when dashboard/.env leaves the key out."""
    base = {"APP_KEY": "base64:abcdefghij", "APP_DEBUG": "false", "DB_HOST": "127.0.0.1", "DB_PORT": "3306",
            "DB_DATABASE": "automation_db", "DB_USERNAME": "laqta_app", "DB_PASSWORD": SECRETS["DB_PASSWORD"]}
    root = make_root(tmp_path / "a", dash=base)                      # no APP_ENV line at all
    assert any(r.status == sc.WARN and "APP_ENV" in r.text for r in sc.check_env(str(root), environ={}))
    write_cached_config(root, env="production")
    assert not any("APP_ENV" in r.text for r in sc.check_env(str(root), environ={}))
    write_cached_config(root, env="local")
    assert any(r.status == sc.WARN and "APP_ENV" in r.text for r in sc.check_env(str(root), environ={}))


def test_cached_config_reader_gives_none_without_a_cache(tmp_path):
    assert sc.cached_config(str(tmp_path)) == {"env": None, "debug": None, "secure": None}
    write_cached_config(tmp_path, env="production", debug="false", secure="true")
    assert sc.cached_config(str(tmp_path)) == {"env": "production", "debug": False, "secure": True}


def test_https_site_needs_a_secure_session_cookie(tmp_path):
    https = SITE.replace("listen 80;", "listen 443 ssl;\n    ssl_certificate /x.pem;")
    (tmp_path / "web").mkdir()
    web = nginx_dir(tmp_path / "web", https)
    root = make_root(tmp_path / "app")                               # dashboard/.env has no SESSION_SECURE_COOKIE
    warn = [r for r in sc.check_web(web, root=str(root)) if r.status == sc.WARN]
    assert len(warn) == 1 and "SESSION_SECURE_COOKIE" in warn[0].text
    write_cached_config(root, secure="true")                         # frozen by install.sh on the HTTPS path
    assert statuses(sc.check_web(web, root=str(root))) == [sc.OK, sc.OK]
    write_cached_config(root, secure="NULL")                         # frozen without it
    assert any(r.status == sc.WARN for r in sc.check_web(web, root=str(root)))
    assert statuses(sc.check_web(web)) == [sc.OK, sc.OK]             # no root given: the old behaviour
    local = SITE.replace("listen 80;", "listen 127.0.0.1:8080;")    # a tunnel on plain http: no Secure cookie wanted
    (tmp_path / "web2").mkdir()
    assert statuses(sc.check_web(nginx_dir(tmp_path / "web2", local), root=str(root))) == [sc.OK, sc.OK]


# ------------------------------------------------------------------ the whole run
def deps_for(tmp_path, tables=("resolved_products", "system_settings", "automation_queue"), systemd=True, **extra):
    gb = 1024 ** 3
    deps = {
        "python": {"version_info": (3, 12, 3), "prefix": str(tmp_path / ".venv")},
        "connect": lambda **k: FakeConn(list(tables), {}),
        "open_sheet": lambda: (object(), object()),
        "find_spec": lambda name: None,
        "listdir": lambda folder: [],
        "disk_usage": lambda p: Usage(100 * gb, 50 * gb, 50 * gb),
        "which": (lambda n: "/bin/systemctl") if systemd else (lambda n: None),
        "isdir": lambda p: systemd,
        "run": fake_systemctl(active=ALL_ACTIVE),
    }
    deps.update(extra)
    return deps


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in Path(root).rglob("*"))


def run_main(argv, root, deps, environ=None):
    import io
    out = io.StringIO()
    code = sc.main(["--root", str(root), *argv], deps=deps, environ=environ if environ is not None else {}, out=out)
    return code, out.getvalue()


def test_main_all_good_exits_zero_prints_arabic_and_changes_nothing(tmp_path, offline):
    root = make_root(tmp_path / "app")
    web = tmp_path / "nginx"
    web.mkdir()
    deps = deps_for(root, nginx_dir=nginx_dir(web, SITE.replace("listen 80;", "listen 127.0.0.1:8080;")))
    before = snapshot(root)
    code, text = run_main(["--skip-sheet"], root, deps)
    assert code == 0, text
    lines = text.splitlines()
    assert any(line.startswith("✅") for line in lines) and not any(line.startswith("❌") for line in lines)
    assert "الخلاصة" in text and "كل شي تمام" in text
    for value in SECRETS.values():
        assert value not in text
    assert snapshot(root) == before                      # read-only
    assert not list(Path(root).rglob("*.tmp"))


def test_main_exits_one_when_anything_fails(tmp_path, offline):
    root = make_root(tmp_path / "app", env={k: v for k, v in GOOD_ENV.items() if k != "SERPER_API_KEY"})
    web = tmp_path / "nginx"
    web.mkdir()
    code, text = run_main(["--skip-sheet"], root, deps_for(root, nginx_dir=nginx_dir(web)))
    assert code == 1 and any(line.startswith("❌") for line in text.splitlines()) and "SERPER_API_KEY" in text


def test_main_only_runs_the_named_parts(tmp_path, offline):
    root = make_root(tmp_path / "app")
    code, text = run_main(["--only", "env"], root, deps_for(root))
    assert code == 0
    assert "ملفات الإعدادات" in text and "قاعدة البيانات" not in text and "Cloudinary" not in text
    code, text = run_main(["--only", "disk", "--only", "python"], root, deps_for(root))
    assert "مساحة القرص" in text and "بايثون" in text and "ملفات الإعدادات" not in text


def test_main_without_systemd_skips_units_and_web(tmp_path, offline):
    root = make_root(tmp_path / "app")
    code, text = run_main(["--skip-sheet"], root, deps_for(root, systemd=False))
    assert text.count("مو سيرفر systemd") == 2 and code == 0
    code, text = run_main(["--skip-sheet", "--no-systemd"], root, deps_for(root))
    assert text.count("مو سيرفر systemd") == 2


def test_main_sheet_failure_is_reported(tmp_path, offline):
    root = make_root(tmp_path / "app")

    def denied():
        raise PermissionError("not shared with the service account")

    code, text = run_main(["--only", "sheet"], root, deps_for(root, open_sheet=denied))
    assert code == 1 and "ما قدرت أفتح الشيت" in text


def test_main_db_settings_feed_the_other_checks(tmp_path, offline):
    root = make_root(tmp_path / "app", env={k: v for k, v in GOOD_ENV.items() if not k.startswith("CLOUDINARY_API")})
    conn = FakeConn(["resolved_products", "system_settings", "automation_queue"],
                    {"cloudinary_api_key": "saved-from-dashboard", "cloudinary_api_secret": "saved-from-dashboard-2"})
    code, text = run_main(["--only", "cloudinary"], root, deps_for(root, connect=lambda **k: conn))
    assert code == 0 and any(line.startswith("✅") for line in text.splitlines()) and "saved-from-dashboard" not in text


def test_script_runs_as_a_program_without_importing_the_app(tmp_path):
    """The real entry point: --help works and the module has no import-time side effects."""
    import subprocess
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "server_check.py"), "--help"],
                          capture_output=True, text=True, timeout=30, env={**os.environ, "PYTHONUTF8": "1"},
                          encoding="utf-8")
    assert done.returncode == 0 and "--only" in done.stdout and "--skip-sheet" in done.stdout
