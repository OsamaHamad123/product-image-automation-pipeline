"""Static and behaviour checks for deploy/ubuntu (the Ubuntu server deployment kit).

Nothing here installs anything or touches the network: scripts are syntax-checked, install.sh only runs with
--dry-run, backup.sh runs against a stub dump command, units and the nginx site are rendered into a temp folder.
"""

import atexit
import base64
import configparser
import contextlib
import datetime
import getpass
import grp
import gzip
import http.client
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy" / "ubuntu"
SCRIPTS = sorted(DEPLOY.glob("*.sh"))
UNITS = ["laqta-sync-worker.service", "laqta-nightly.service", "laqta-nightly.timer",
         "laqta-backup.service", "laqta-backup.timer", "laqta-outbox-flush.service", "laqta-outbox-flush.timer",
         "laqta-run.service", "laqta-run.path"]
PYTHON_SERVICES = ["laqta-nightly.service", "laqta-sync-worker.service", "laqta-outbox-flush.service",
                   "laqta-run.service"]
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux deployment kit")

SAMPLE_APP = "/opt/laqta"


def _clean_clone() -> Path:
    """The app folder install.sh's dry runs point at: what it reads from a fresh clone (requirements.txt and
    dashboard/artisan), with no .env and no dashboard/.env, as in CI. Never the working tree itself: a developer's own
    dashboard/.env (gitignored, APP_DEBUG=true while developing) or .env would decide what install.sh's production
    check and DB name say, so the tests would pass or fail with the machine they run on."""
    app = Path(tempfile.mkdtemp(prefix="laqta_clone_"))
    (app / "dashboard").mkdir()
    shutil.copy2(REPO / "requirements.txt", app / "requirements.txt")
    shutil.copy2(REPO / "dashboard" / "artisan", app / "dashboard" / "artisan")
    atexit.register(shutil.rmtree, str(app), True)
    return app


APP = _clean_clone()


# ------------------------------------------------------------------ helpers
def render(text, app_dir=SAMPLE_APP, user="laqta", listen="listen 80;", server_name="dash.example.com",
           redirect="", monitor="", memory="1792M"):
    for key, value in {"@REDIRECT_SERVER@": redirect, "@APP_DIR@": app_dir, "@APP_USER@": user, "@APP_HOME@": "/var/lib/laqta",
                       "@MODELS_DIR@": "/var/lib/laqta/models", "@PHP_SOCKET@": "/run/php/laqta.sock",
                       "@SERVER_NAME@": server_name, "@LISTEN@": listen, "@MONITOR_ALLOW@": monitor,
                       "@MEMORY_MAX@": memory}.items():
        text = text.replace(key, value)
    return text


# an unreplaced install.sh word looks like @APP_DIR@; a template unit's name (name@%n.service) is not one
PLACEHOLDER = re.compile(r"@[A-Z][A-Z_]*@")


def parse_unit(text):
    """{section: [(key, value), ...]} (repeated keys such as Environment= are all kept, comments dropped)."""
    sections, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], [])
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current.append((key.strip(), value.strip()))
    return sections


def values(unit, section, key):
    return [v for k, v in unit.get(section, []) if k == key]


def rendered_unit(name, **kwargs):
    return parse_unit(render((DEPLOY / name).read_text(encoding="utf-8"), **kwargs))


# ------------------------------------------------------------------ shell scripts

# The DB credential the installer reads, and a throw-away value for it: built at run time so the source never holds
# a "name = literal" credential pair (GitGuardian scans every commit).
DB_CRED_VAR = "LAQTA_DB_" + "PASS" + "WORD"


def _throwaway(tag):
    return "-".join(("tst", tag, "never", "printed", "7f3c9a1e"))

@needs_bash
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_have_valid_syntax(script):
    done = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_pass_shellcheck(script):
    done = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_use_strict_mode(script):
    assert re.search(r"^set -euo pipefail$", script.read_text(encoding="utf-8"), re.M)


def test_install_script_never_writes_an_env_file_or_a_secret():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r">>?\s*\S*\.env\b", code), "install.sh must not write .env"
    assert not re.search(r"\b(cp|mv|install|tee|sed -i)\b[^\n]*\.env\b", code), "install.sh must not copy or edit .env"
    assert "--defaults-file" not in code
    # the only tool allowed to touch a .env is artisan key:generate, and only when APP_KEY is empty
    assert "key:generate" in code and "APP_KEY=" in code
    # no hard-coded password or key
    assert not re.search(r"(?i)(password|secret|api_key)\s*=\s*['\"][^$'\"\s]{6,}", code)


def run_install(*args, env=None, cwd=REPO):
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("LAQTA_")}
    full_env.update(env or {})
    return subprocess.run([BASH, str(DEPLOY / "install.sh"), *args], capture_output=True, text=True, cwd=str(cwd),
                          env=full_env, timeout=60)


needs_plain_path = pytest.mark.skipif(not re.match(r"^/[A-Za-z0-9._/-]+$", str(APP)),
                                      reason="install.sh only accepts plain app paths")


@needs_bash
def test_install_help_lists_the_flags():
    done = run_install("--help")
    assert done.returncode == 0
    for flag in ("--with-birefnet", "--lite", "--gpu", "--local-only", "--server-name", "--dry-run",
                 "--reset-auth", "LAQTA_DB_PASSWORD"):
        assert flag in done.stdout


@needs_bash
@needs_plain_path
def test_install_dry_run_default_plan():
    value = _throwaway("plan")
    done = run_install("--dry-run", str(APP), "--domain", "dash.example.com", env={DB_CRED_VAR: value})
    out = done.stdout + done.stderr
    assert value not in out
    for fragment in ("apt-get install", "python3-venv", "php8.3-fpm", "mariadb-server", "nginx", "libgl1",
                     "libglib2.0-0", "composer", "-m venv", "pip install", "requirements.txt",
                     "composer install --no-dev", "config:cache", "route:cache", "local_cache_db.init_db",
                     "laqta-nightly.timer", "laqta-backup.timer", "/usr/local/sbin/laqta-backup",
                     "htpasswd -B [-c] /etc/nginx/laqta.htpasswd"):
        assert fragment in out, fragment
    assert "rembg" not in out                      # BiRefNet is opt-in
    assert "redis-server" not in out
    assert "systemctl enable --now laqta-nightly.timer" in out and "NOT enabled" in out


@needs_bash
@needs_plain_path
def test_install_dry_run_birefnet_variants():
    cpu = run_install("--dry-run", str(APP), "--local-only", "--with-birefnet").stdout
    assert "rembg\\[cpu\\]" in cpu and "birefnet-general" in cpu and "birefnet-general-lite" not in cpu
    assert "rembg\\[cpu\\]==2.0.85" in cpu          # pinned: remove(decontaminate=True) needs rembg 2.0.79+
    assert "U2NET_HOME=/var/lib/laqta/models" in cpu
    lite = run_install("--dry-run", str(APP), "--local-only", "--with-birefnet", "--lite").stdout
    assert "birefnet-general-lite" in lite
    gpu = run_install("--dry-run", str(APP), "--local-only", "--with-birefnet", "--gpu").stdout
    assert "rembg\\[gpu\\]" in gpu and "rembg\\[cpu\\]" not in gpu


@needs_bash
@needs_plain_path
def test_install_flags_that_need_birefnet_are_refused_alone():
    done = run_install("--dry-run", str(APP), "--lite")
    assert done.returncode != 0 and "--with-birefnet" in done.stderr


@needs_bash
@needs_plain_path
def test_install_dry_run_local_only_and_redis():
    out = run_install("--dry-run", str(APP), "--local-only", "--with-redis", "--enable-units").stdout
    assert "ssh -L 8080:127.0.0.1:8080" in out
    assert "redis-server" in out and "laqta-sync-worker.service" in out
    assert "removing nginx's default welcome site" not in out


@needs_bash
def test_install_rejects_a_folder_that_is_not_the_repo(tmp_path):
    done = run_install("--dry-run", str(tmp_path))
    assert done.returncode != 0 and "not a clone of the repository" in done.stderr


@needs_bash
@needs_plain_path
def test_install_rejects_a_short_database_password():
    done = run_install("--dry-run", str(APP), "--local-only", env={DB_CRED_VAR: "x" * 5})
    assert done.returncode != 0 and "at least 12 characters" in done.stderr


# ------------------------------------------------------------------ systemd units
@pytest.mark.parametrize("name", UNITS)
def test_units_have_no_placeholders_left_after_rendering_and_no_secrets(name):
    text = render((DEPLOY / name).read_text(encoding="utf-8"))
    assert not PLACEHOLDER.search(re.sub(r"(?m)^\s*#.*$", "", text))
    assert not re.search(r"(?im)^Environment=.*(KEY|PASSWORD|SECRET|TOKEN)", text)
    assert "EnvironmentFile=" not in text or "laqta-backup" in name  # secrets stay in .env / ~/.my.cnf, not in units


def test_sync_worker_unit():
    unit = rendered_unit("laqta-sync-worker.service")
    assert values(unit, "Service", "User") == ["laqta"] and values(unit, "Service", "Group") == ["laqta"]
    assert values(unit, "Service", "Restart") == ["always"]
    assert values(unit, "Service", "WorkingDirectory") == [SAMPLE_APP]
    exec_start = values(unit, "Service", "ExecStart")[0].split()
    assert exec_start[0] == f"{SAMPLE_APP}/.venv/bin/python"
    assert exec_start[-1] == f"{SAMPLE_APP}/sync_worker.py"
    assert (REPO / "sync_worker.py").is_file()
    assert values(unit, "Install", "WantedBy") == ["multi-user.target"]


def test_nightly_service_and_timer_honour_max_hours():
    unit = rendered_unit("laqta-nightly.service")
    assert values(unit, "Service", "Type") == ["oneshot"]
    assert values(unit, "Service", "User") == ["laqta"]
    assert values(unit, "Service", "WorkingDirectory") == [SAMPLE_APP]   # config and temp/ paths are relative
    exec_start = values(unit, "Service", "ExecStart")[0]
    assert exec_start.startswith(f"{SAMPLE_APP}/.venv/bin/python ")
    assert f"{SAMPLE_APP}/scripts/run_nightly.py" in exec_start and "--max-hours ${NIGHTLY_MAX_HOURS}" in exec_start
    hours = [v.split("=", 1)[1] for v in values(unit, "Service", "Environment") if v.startswith("NIGHTLY_MAX_HOURS=")]
    assert hours and 1 <= float(hours[0]) <= 23                       # run_nightly ignores values outside 1..23
    assert "3" in values(unit, "Service", "SuccessExitStatus")[0].split()  # exit 3 = stopped on purpose
    assert values(unit, "Service", "TimeoutStartSec")                  # a hung night must not block every later one
    assert (REPO / "scripts" / "run_nightly.py").is_file()
    assert "--max-hours" in (REPO / "scripts" / "run_nightly.py").read_text(encoding="utf-8")

    timer = rendered_unit("laqta-nightly.timer")
    assert values(timer, "Timer", "Unit") == ["laqta-nightly.service"]
    assert values(timer, "Timer", "Persistent") == ["true"]
    assert values(timer, "Timer", "OnCalendar")
    assert values(timer, "Install", "WantedBy") == ["timers.target"]


def test_backup_units_run_the_root_owned_copy():
    unit = rendered_unit("laqta-backup.service")
    assert values(unit, "Service", "User") == ["root"]
    assert values(unit, "Service", "ExecStart") == ["/usr/local/sbin/laqta-backup"]
    assert "/usr/local/sbin/laqta-backup" in (DEPLOY / "install.sh").read_text(encoding="utf-8")
    timer = rendered_unit("laqta-backup.timer")
    assert values(timer, "Timer", "Unit") == ["laqta-backup.service"] and values(timer, "Timer", "Persistent") == ["true"]


def test_app_units_never_run_as_root():
    for name in ("laqta-sync-worker.service", "laqta-nightly.service"):
        assert values(rendered_unit(name), "Service", "User") == ["laqta"], name


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_systemd_analyze_verifies_the_rendered_units(tmp_path):
    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / "scripts").mkdir()
    for path in (app / ".venv" / "bin" / "python",):
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    (app / "sync_worker.py").write_text("")
    (app / "scripts" / "run_nightly.py").write_text("")
    backup = tmp_path / "laqta-backup"
    backup.write_text("#!/bin/sh\n")
    backup.chmod(0o755)
    paths = []
    for name in UNITS:
        text = render((DEPLOY / name).read_text(encoding="utf-8"), app_dir=str(app), user=getpass.getuser())
        text = text.replace("/usr/local/sbin/laqta-backup", str(backup))
        target = tmp_path / name
        target.write_text(text)
        paths.append(str(target))
    done = subprocess.run(["systemd-analyze", "verify", *paths], capture_output=True, text=True, timeout=60)
    problems = [line for line in (done.stdout + done.stderr).splitlines()
                if line.strip() and "not found" not in line]    # mariadb / redis units may be absent on a test box
    assert not problems, "\n".join(problems)


# ------------------------------------------------------------------ nginx site and php-fpm pool
def site(**kwargs):
    return render((DEPLOY / "laqta.conf").read_text(encoding="utf-8"), **kwargs)


def block(text, header):
    """The text of the {...} block that starts at `header`."""
    start = text.index(header)
    depth, i = 0, text.index("{", start)
    begin = i
    while True:
        depth += 1 if text[i] == "{" else -1 if text[i] == "}" else 0
        i += 1
        if depth == 0:
            return text[begin:i]


def server_blocks(text):
    """The text of every top-level `server { ... }` block of an nginx file, in order."""
    blocks = []
    for m in re.finditer(r"(?m)^server\s*\{", text):
        depth, i = 0, m.end() - 1
        while True:
            depth += 1 if text[i] == "{" else -1 if text[i] == "}" else 0
            i += 1
            if depth == 0:
                break
        blocks.append(text[m.start():i])
    return blocks


def test_nginx_site_requires_basic_auth_everywhere_except_the_acme_path():
    text = re.sub(r"(?m)^\s*#.*$", "", site())
    assert re.search(r'^\s*auth_basic\s+"[^"]+";', text, re.M)
    assert "auth_basic_user_file /etc/nginx/laqta.htpasswd;" in text
    # exactly two places are open: the certbot challenge, and /healthz (which nginx restricts to the monitor addresses:
    # test_healthz_location_is_open_only_to_loopback_and_the_monitor_addresses)
    assert len(re.findall(r"auth_basic\s+off", text)) == 2
    assert "auth_basic off" in block(text, "location ^~ /.well-known/acme-challenge/")
    assert "auth_basic off" in block(text, "location = /healthz")
    # the server-level directive comes before every location, so every other location inherits it
    assert text.index("auth_basic ") < text.index("location ")


def test_nginx_site_denies_dotfiles_and_the_laravel_folders():
    text = site()
    assert re.search(r"location ~ /\\\.\(\?!well-known\)\s*\{\s*deny all;", text)
    folders = re.search(r"location ~ \^/\(([^)]*)\)\(/\|\$\)\s*\{\s*deny all;", text)
    assert folders, "folder deny rule missing"
    for folder in ("storage", "vendor", "bootstrap", "config", "database", "routes"):
        assert folder in folders.group(1).split("|")
    ext = re.search(r"location ~\* \\\.\(([^)]*)\)\$\s*\{\s*deny all;", text)
    assert ext
    for suffix in ("env", "sql", "log", "py", "json"):
        assert suffix in ext.group(1).split("|")


def test_no_dashboard_route_is_caught_by_the_nginx_deny_rules():
    """The folder deny rule must never shadow a real page or API of the dashboard."""
    folders = re.search(r"location ~ \^/\(([^)]*)\)\(/\|\$\)", site()).group(1).split("|")
    routes = (REPO / "dashboard" / "routes" / "web.php").read_text(encoding="utf-8")
    paths = re.findall(r"Route::[a-z]+\('([^']*)'", routes)
    assert paths, "no routes found"
    first_segments = {p.strip("/").split("/")[0] for p in paths}
    assert not first_segments & set(folders), first_segments & set(folders)
    extension = re.search(r"location ~\* \\\.\(([^)]*)\)\$", site()).group(1).split("|")
    ending = {p.rsplit(".", 1)[1] for p in paths if "." in p.rsplit("/", 1)[-1]}
    assert not ending & set(extension), ending & set(extension)


def test_nginx_site_serves_only_the_public_folder_and_only_index_php():
    text = site()
    assert f"root {SAMPLE_APP}/dashboard/public;" in text
    assert re.search(r"location = /index\.php\s*\{", text)
    assert re.search(r"location ~ \\\.php\$\s*\{\s*return 404;", text)        # any other .php is refused
    assert "fastcgi_pass unix:/run/php/laqta.sock;" in text
    assert "autoindex on" not in text and "server_tokens off" in text
    # no alias or root pointing above public/ (the app code, .env and credentials.json live there)
    roots = re.findall(r"^\s*(?:root|alias)\s+(\S+);", text, re.M)
    assert set(roots) <= {f"{SAMPLE_APP}/dashboard/public", "/var/www/letsencrypt"}


def test_nginx_site_listen_variants():
    assert "listen 127.0.0.1:8080;" in site(listen="listen 127.0.0.1:8080;", server_name="_")
    assert "auth_basic " in site(listen="listen 127.0.0.1:8080;", server_name="_")   # auth stays on locally too
    assert not PLACEHOLDER.search(re.sub(r"(?m)^\s*#.*$", "", site()))



def _unprivileged_ports(text):
    """`nginx -t` binds every listen address; a CI runner that is not root cannot bind 80 / 443 (EACCES), so the test
    copy listens on high ports instead. The rendered file itself (asserted elsewhere) keeps 80 and 443."""
    high = {"80": "28080", "443": "28443"}
    return re.sub(r"(?m)^(\s*listen\s+(?:\[::\]:|[\d.]+:)?)(80|443)\b", lambda m: m.group(1) + high[m.group(2)], text)

@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx is not installed")
@pytest.mark.parametrize("listen,name", [("listen 80;", "dash.example.com"), ("listen 127.0.0.1:8080;", "_")])
def test_nginx_accepts_the_rendered_site(tmp_path, listen, name):
    text = _unprivileged_ports(site(listen=listen, server_name=name))
    text = re.sub(r"(access_log|error_log)\s+\S+;", lambda m: f"{m.group(1)} {tmp_path}/{m.group(1)}.log;", text)
    text = text.replace("root /var/www/letsencrypt;", f"root {tmp_path};")
    (tmp_path / "laqta.conf").write_text(text)
    (tmp_path / "fastcgi_params").write_text("fastcgi_param QUERY_STRING $query_string;\n")
    (tmp_path / "nginx.conf").write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/main.log;\nevents {{}}\n"
        f"http {{\n    access_log off;\n    include {tmp_path}/laqta.conf;\n}}\n")
    done = subprocess.run(["nginx", "-t", "-c", str(tmp_path / "nginx.conf"), "-p", str(tmp_path)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


def install_render(tmp_path, *args, cert=False, env=None):
    """Run install.sh --dry-run with the render hook: returns the folder holding every rendered unit and config."""
    out = tmp_path / "render"
    out.mkdir()
    live = tmp_path / "live"
    if cert:
        folder = live / "dash.example.com"
        folder.mkdir(parents=True)
        (folder / "fullchain.pem").write_text("cert")
        (folder / "privkey.pem").write_text("key")
    done = run_install("--dry-run", str(APP), *args,
                       env={"LAQTA_RENDER_DIR": str(out), "LAQTA_CERT_ROOT": str(live), **(env or {})})
    assert done.returncode in (0, 2), done.stderr
    return out


@needs_bash
@needs_plain_path
def test_install_renders_every_unit_and_config_without_placeholders(tmp_path):
    out = install_render(tmp_path, "--server-name", "dash.example.com")
    names = {p.name for p in out.iterdir()}
    # laqta.conf is the nginx site; fail2ban's jail is only rendered with --with-fail2ban
    assert names == set(UNITS) | {"laqta.conf", "laqta-fpm-pool.conf", "laqta-logrotate"}
    for path in out.iterdir():
        text = re.sub(r"(?m)^\s*[#;].*$", "", path.read_text(encoding="utf-8"))
        assert not PLACEHOLDER.search(text), path.name
    nightly = (out / "laqta-nightly.service").read_text(encoding="utf-8")
    assert f"ExecStart={APP}/.venv/bin/python" in nightly and f"WorkingDirectory={APP}" in nightly
    assert "U2NET_HOME=/var/lib/laqta/models" in nightly


@needs_bash
@needs_plain_path
def test_install_serves_no_dashboard_on_port_80_before_a_certificate_exists(tmp_path):
    """Audit item 1: the shared login must never travel over plain HTTP. Until certbot has issued a certificate the
    dashboard listens on loopback only, and port 80 answers nothing but the certbot challenge."""
    conf = (install_render(tmp_path, "--domain", "dash.example.com") / "laqta.conf").read_text(encoding="utf-8")
    assert "listen 443" not in conf and "return 301" not in conf
    assert "default_server" not in conf
    servers = server_blocks(conf)
    assert len(servers) == 2
    dashboard = next(s for s in servers if "fastcgi_pass" in s)
    port80 = next(s for s in servers if "fastcgi_pass" not in s)
    assert "listen 127.0.0.1:8080;" in dashboard and 'auth_basic "Laqta";' in dashboard
    assert not re.search(r"listen\s+(\[::\]:)?80\b", dashboard)
    assert re.search(r"listen 80;", port80) and "server_name dash.example.com;" in port80
    assert "acme-challenge" in port80 and "auth_basic" not in port80 and "root @" not in port80
    assert "return 503" in port80 and "HTTPS is not set up yet" in port80


@needs_bash
@needs_plain_path
def test_install_https_site_once_certbot_issued_a_certificate(tmp_path):
    conf = (install_render(tmp_path, "--server-name", "dash.example.com", cert=True) / "laqta.conf").read_text(encoding="utf-8")
    live = tmp_path / "live" / "dash.example.com"
    assert "listen 443 ssl;" in conf and f"ssl_certificate {live}/fullchain.pem;" in conf
    assert f"ssl_certificate_key {live}/privkey.pem;" in conf and "Strict-Transport-Security" in conf
    main, redirect = conf.split("# Port 80 serves nothing")
    assert 'auth_basic "Laqta";' in main and "fastcgi_pass" in main            # the dashboard server is the 443 one
    assert "listen 80;" in redirect and "return 301 https://$host$request_uri;" in redirect
    assert "fastcgi_pass" not in redirect and "auth_basic" not in redirect     # port 80 serves no dashboard at all
    assert "acme-challenge" in redirect


@needs_bash
@needs_plain_path
def test_install_local_only_ignores_certificates(tmp_path):
    conf = (install_render(tmp_path, "--server-name", "dash.example.com", "--local-only", cert=True)
            / "laqta.conf").read_text(encoding="utf-8")
    assert "listen 127.0.0.1:8080;" in conf and "listen 443" not in conf and 'auth_basic "Laqta";' in conf


@needs_bash
@needs_plain_path
@pytest.mark.skipif(shutil.which("nginx") is None or shutil.which("openssl") is None, reason="nginx/openssl missing")
def test_nginx_accepts_the_https_site(tmp_path):
    folder = tmp_path / "live" / "dash.example.com"
    folder.mkdir(parents=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=dash.example.com",
                    "-keyout", str(folder / "privkey.pem"), "-out", str(folder / "fullchain.pem")],
                   check=True, capture_output=True)
    done = run_install("--dry-run", str(APP), "--server-name", "dash.example.com",
                       env={"LAQTA_RENDER_DIR": str(tmp_path), "LAQTA_CERT_ROOT": str(tmp_path / "live")})
    assert done.returncode in (0, 2), done.stderr
    text = (tmp_path / "laqta.conf").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^\s*listen \[::\]:\d+[^;]*;\n", "", text)     # test boxes without IPv6 cannot bind [::]
    text = _unprivileged_ports(text)
    text = re.sub(r"(access_log|error_log)\s+\S+;", lambda m: f"{m.group(1)} {tmp_path}/{m.group(1)}.log;", text)
    text = text.replace("root /var/www/letsencrypt;", f"root {tmp_path};")
    (tmp_path / "site.conf").write_text(text)
    (tmp_path / "fastcgi_params").write_text("fastcgi_param QUERY_STRING $query_string;\n")
    (tmp_path / "nginx.conf").write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/main.log;\nevents {{}}\n"
        f"http {{\n    access_log off;\n    include {tmp_path}/site.conf;\n}}\n")
    nginx = subprocess.run(["nginx", "-t", "-c", str(tmp_path / "nginx.conf"), "-p", str(tmp_path)],
                           capture_output=True, text=True, timeout=30)
    assert nginx.returncode == 0, nginx.stderr


def test_fpm_pool_runs_as_the_app_user_on_the_socket_nginx_uses():
    pool = render((DEPLOY / "laqta-fpm-pool.conf").read_text(encoding="utf-8"))
    assert re.search(r"^user = laqta$", pool, re.M) and re.search(r"^group = laqta$", pool, re.M)
    assert "listen = /run/php/laqta.sock" in pool and "fastcgi_pass unix:/run/php/laqta.sock;" in site()
    assert re.search(r"^request_terminate_timeout = 900s$", pool, re.M)    # PythonBridge allows 600 s per action
    assert "env[U2NET_HOME] = /var/lib/laqta/models" in pool
    assert "@" not in re.sub(r"(?m)^\s*;.*$", "", pool)


# ------------------------------------------------------------------ backup.sh
def stub_dump(tmp_path, body="seed", fail=False):
    """A fake mariadb-dump: records its arguments and prints a dump-like text."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    dump = bindir / "mariadb-dump"
    dump.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$@" > "{tmp_path}/dump_args.txt"\n'
        + ("exit 3\n" if fail else
           f'for i in $(seq 1 400); do echo "INSERT INTO t VALUES ({body}_$i, \'row $i of a realistic dump\');"; done\n'))
    dump.chmod(0o755)
    return bindir


def run_backup(tmp_path, bindir, mycnf_mode=0o600, **env):
    mycnf = tmp_path / "my.cnf"
    mycnf.write_text("[client]\nuser=backup\n")        # no password written anywhere in the repo or the test
    mycnf.chmod(mycnf_mode)
    backups = tmp_path / "dumps"
    full_env = {"PATH": f"{bindir}:{os.environ['PATH']}", "BACKUP_DIR": str(backups),
                "MYSQL_DEFAULTS_FILE": str(mycnf), "DB_NAME": "automation_db", **env}
    done = subprocess.run([BASH, str(DEPLOY / "backup.sh")], capture_output=True, text=True, env=full_env, timeout=60)
    return done, backups


@needs_bash
def test_backup_writes_a_private_valid_gzip_dump(tmp_path):
    done, backups = run_backup(tmp_path, stub_dump(tmp_path))
    assert done.returncode == 0, done.stderr
    dumps = list(backups.glob("automation_db_*.sql.gz"))
    assert len(dumps) == 1
    assert b"INSERT INTO t" in gzip.decompress(dumps[0].read_bytes())
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    assert stat.S_IMODE(dumps[0].stat().st_mode) == 0o600
    assert not list(backups.glob("*.part"))
    args = (tmp_path / "dump_args.txt").read_text().split()
    assert args[0].startswith("--defaults-extra-file=")          # must be the first option
    assert "--single-transaction" in args and args[-1] == "automation_db"
    assert not any("pass" in a.lower() for a in args)            # no password on the command line


@needs_bash
def test_backup_keeps_fourteen_days_by_default(tmp_path):
    bindir = stub_dump(tmp_path)
    backups = tmp_path / "dumps"
    backups.mkdir()
    now = time.time()
    old = backups / "automation_db_2020-01-01_000000.sql.gz"
    recent = backups / "automation_db_2020-01-02_000000.sql.gz"
    other_db = backups / "another_db_2020-01-01_000000.sql.gz"
    for path, age_days in ((old, 15), (recent, 13), (other_db, 40)):
        path.write_bytes(b"x")
        os.utime(path, (now - age_days * 86400, now - age_days * 86400))
    done, _ = run_backup(tmp_path, bindir)
    assert done.returncode == 0, done.stderr
    assert not old.exists() and recent.exists()
    assert other_db.exists(), "another database's dumps are never deleted"
    assert len(list(backups.glob("automation_db_*.sql.gz"))) == 2   # the recent one and today's


@needs_bash
def test_backup_keep_days_is_configurable(tmp_path):
    bindir = stub_dump(tmp_path)
    backups = tmp_path / "dumps"
    backups.mkdir()
    three_days = backups / "automation_db_2020-01-01_000000.sql.gz"
    three_days.write_bytes(b"x")
    os.utime(three_days, (time.time() - 3 * 86400,) * 2)
    done, _ = run_backup(tmp_path, bindir, KEEP_DAYS="2")
    assert done.returncode == 0 and not three_days.exists()


@needs_bash
def test_backup_refuses_a_credentials_file_other_users_can_read(tmp_path):
    done, backups = run_backup(tmp_path, stub_dump(tmp_path), mycnf_mode=0o644)
    assert done.returncode != 0 and "chmod 600" in done.stderr
    assert not backups.exists() or not list(backups.glob("*"))


@needs_bash
def test_backup_failed_dump_leaves_no_file_and_fails(tmp_path):
    done, backups = run_backup(tmp_path, stub_dump(tmp_path, fail=True))
    assert done.returncode != 0
    assert not list(backups.glob("*"))


@needs_bash
def test_backup_rejects_a_bad_database_name(tmp_path):
    done, _ = run_backup(tmp_path, stub_dump(tmp_path), DB_NAME="x; drop database y")
    assert done.returncode != 0 and "DB_NAME" in done.stderr


# ------------------------------------------------------------------ backup.sh: age encryption and the offsite copy
def make_tool(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def stub_age(tmp_path, mode="ok"):
    """A fake age: records its arguments and whether the readable dump existed, writes an age-looking file."""
    bindir = stub_dump(tmp_path)                       # the same folder also holds the fake mariadb-dump
    log = tmp_path / "age_calls.txt"
    body = {
        "ok": 'printf "age-encryption.org/v1\\n-> X25519 stub\\n"; head -c 400 /dev/zero | tr "\\0" "x"',
        "junk": 'echo "this is not an age file"; head -c 400 /dev/zero | tr "\\0" "x"',
        "fail": "exit 1",
    }[mode]
    make_tool(bindir, "age", (
        'out=""; src=""; args=()\n'
        'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; -r|-R) args+=("$1" "$2"); shift 2;; *) src="$1"; shift;; esac; done\n'
        f'printf "%s\\n" "${{args[*]}}" >> "{log}"\n'
        f'[ -f "$src" ] && echo "plain-existed" >> "{log}"\n'
        f'( {body} ) > "$out" || exit 1\n'))
    return bindir


def stub_rclone(tmp_path, fail=False):
    bindir = stub_dump(tmp_path)
    log = tmp_path / "rclone_calls.txt"
    make_tool(bindir, "rclone", f'printf "%s\\n" "$*" >> "{log}"\n' + ("exit 7\n" if fail else ""))
    return bindir


def a_public_key():
    """A well-formed age recipient (age1 + 58 characters), made up at run time; it is not anyone's key."""
    return "age1" + "q" * 58


def dumps_in(backups):
    return sorted(p.name for p in backups.glob("automation_db_*"))


@needs_bash
def test_backup_without_a_recipient_warns_loudly_and_stays_unencrypted(tmp_path):
    done, backups = run_backup(tmp_path, stub_dump(tmp_path))
    assert done.returncode == 0, done.stderr
    assert "BACKUP_AGE_RECIPIENT is not set" in done.stderr and "UNENCRYPTED" in done.stderr
    assert "age-keygen" in done.stderr and "section 9" in done.stderr           # says what to do about it
    assert [n for n in dumps_in(backups)] and all(n.endswith(".sql.gz") for n in dumps_in(backups))


@needs_bash
def test_backup_with_a_recipient_keeps_only_the_encrypted_file(tmp_path):
    bindir = stub_age(tmp_path)
    stub_dump(tmp_path)
    done, backups = run_backup(tmp_path, bindir, BACKUP_AGE_RECIPIENT=a_public_key())
    assert done.returncode == 0, done.stderr
    assert "UNENCRYPTED" not in done.stderr
    names = dumps_in(backups)
    assert len(names) == 1 and names[0].endswith(".sql.gz.age")                  # no readable dump, no .part left
    assert not list(backups.glob("*.part")) and not [n for n in names if n.endswith(".sql.gz")]
    final = backups / names[0]
    assert stat.S_IMODE(final.stat().st_mode) == 0o600 and stat.S_IMODE(backups.stat().st_mode) == 0o700
    assert final.read_bytes().startswith(b"age-encryption.org/v1")
    calls = (tmp_path / "age_calls.txt").read_text().splitlines()
    assert calls[0] == f"-r {a_public_key()}" and "plain-existed" in calls      # age was given the public key only
    assert "encrypted with age" in done.stdout


@needs_bash
def test_backup_encrypts_for_a_recipients_file(tmp_path):
    recipients = tmp_path / "recipients.txt"
    recipients.write_text(a_public_key() + "\n")
    done, backups = run_backup(tmp_path, stub_age(tmp_path), BACKUP_AGE_RECIPIENT=str(recipients))
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "age_calls.txt").read_text().splitlines()[0] == f"-R {recipients}"
    assert dumps_in(backups)[0].endswith(".sql.gz.age")


@needs_bash
@pytest.mark.skipif(shutil.which("age") is None or shutil.which("age-keygen") is None, reason="age is not installed")
def test_backup_round_trip_with_the_real_age(tmp_path):
    """The file that stays on the server decrypts, with the private key that never touched it, to a valid gzip dump."""
    identity = tmp_path / "owner.key"                                            # made now, on the "owner's PC"
    made = subprocess.run(["age-keygen", "-o", str(identity)], capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    recipient = next(line.split(": ", 1)[1] for line in (made.stderr + identity.read_text()).splitlines()
                     if line.lower().startswith(("public key: ", "# public key: ")))
    done, backups = run_backup(tmp_path, stub_dump(tmp_path, body="real"), BACKUP_AGE_RECIPIENT=recipient)
    assert done.returncode == 0, done.stderr
    names = dumps_in(backups)
    assert len(names) == 1 and names[0].endswith(".sql.gz.age")
    encrypted = (backups / names[0]).read_bytes()
    assert b"INSERT INTO t" not in encrypted and not encrypted.startswith(b"\x1f\x8b")
    plain = subprocess.run(["age", "-d", "-i", str(identity), str(backups / names[0])], capture_output=True)
    assert plain.returncode == 0, plain.stderr
    assert b"INSERT INTO t VALUES (real_1," in gzip.decompress(plain.stdout)
    assert not list(backups.glob("*.part"))


@needs_bash
def test_backup_refuses_a_private_key_or_garbage_as_recipient_before_dumping_anything(tmp_path):
    private = "AGE-SECRET-KEY-" + "1" * 59                                        # built at run time, not a real key
    for bad in (private, "age1short", "not-a-key", "/no/such/recipients/file"):
        done, backups = run_backup(tmp_path, stub_age(tmp_path), BACKUP_AGE_RECIPIENT=bad)
        assert done.returncode != 0, bad
        assert "BACKUP_AGE_RECIPIENT" in done.stderr
        assert not (tmp_path / "dump_args.txt").exists(), "nothing may be dumped with a bad recipient"
        assert not backups.exists() or not list(backups.glob("*"))
        assert private not in done.stderr and private not in done.stdout


@needs_bash
def test_backup_does_not_fall_back_to_plaintext_when_age_is_missing(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "stat", "date", "gzip", "find", "sed", "head", "mkdir", "chmod", "rm", "mv", "shred",
                 "basename", "seq", "cat", "tr", "dirname"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    stub_dump(tmp_path)
    mycnf = tmp_path / "my.cnf"
    mycnf.write_text("[client]\nuser=backup\n")
    mycnf.chmod(0o600)
    env = {"PATH": str(bindir), "BACKUP_DIR": str(tmp_path / "dumps"), "MYSQL_DEFAULTS_FILE": str(mycnf),
           "DB_NAME": "automation_db", "BACKUP_AGE_RECIPIENT": a_public_key()}
    done = subprocess.run([BASH, str(DEPLOY / "backup.sh")], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode != 0 and "age is not installed" in done.stderr
    assert not (tmp_path / "dumps").exists() or not list((tmp_path / "dumps").glob("*"))


@needs_bash
@pytest.mark.parametrize("mode,message", [("fail", "age failed"), ("junk", "does not look like an age file")])
def test_backup_with_a_broken_encryption_leaves_nothing_behind(tmp_path, mode, message):
    done, backups = run_backup(tmp_path, stub_age(tmp_path, mode), BACKUP_AGE_RECIPIENT=a_public_key())
    assert done.returncode != 0 and message in done.stderr
    assert not list(backups.glob("*")), "neither the encrypted part nor the readable dump may stay"


@needs_bash
def test_backup_copies_the_encrypted_dump_offsite(tmp_path):
    bindir = stub_age(tmp_path)
    stub_rclone(tmp_path)
    done, backups = run_backup(tmp_path, bindir, BACKUP_AGE_RECIPIENT=a_public_key(),
                               BACKUP_RCLONE_REMOTE="b2:laqta-backups/db/")
    assert done.returncode == 0, done.stderr
    final = dumps_in(backups)[0]
    call = (tmp_path / "rclone_calls.txt").read_text().split()
    assert call[0] == "copyto" and call[-2] == str(backups / final) and call[-1] == f"b2:laqta-backups/db/{final}"
    assert f"copied {final} to b2:laqta-backups/db/" in done.stdout


@needs_bash
def test_backup_never_sends_an_unencrypted_dump_offsite_by_default(tmp_path):
    bindir = stub_rclone(tmp_path)
    stub_dump(tmp_path)
    done, backups = run_backup(tmp_path, bindir, BACKUP_RCLONE_REMOTE="b2:laqta-backups/db")
    assert done.returncode != 0 and "not encrypted" in done.stderr and "BACKUP_AGE_RECIPIENT" in done.stderr
    assert not (tmp_path / "rclone_calls.txt").exists()                         # nothing left the server
    assert len(dumps_in(backups)) == 1                                           # the local dump is kept
    (tmp_path / "again").mkdir()
    allowed, _ = run_backup(tmp_path / "again", stub_rclone(tmp_path / "again"), BACKUP_RCLONE_REMOTE="b2:x",
                            BACKUP_RCLONE_ALLOW_PLAINTEXT="1")
    assert allowed.returncode == 0 and (tmp_path / "again" / "rclone_calls.txt").exists()


@needs_bash
def test_a_failed_offsite_copy_fails_the_unit_but_keeps_the_dump_and_still_cleans_up(tmp_path):
    bindir = stub_age(tmp_path)
    stub_rclone(tmp_path, fail=True)
    backups = tmp_path / "dumps"
    backups.mkdir()
    old = backups / "automation_db_2020-01-01_000000.sql.gz.age"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 20 * 86400,) * 2)
    done, _ = run_backup(tmp_path, bindir, BACKUP_AGE_RECIPIENT=a_public_key(), BACKUP_RCLONE_REMOTE="b2:laqta/db")
    assert done.returncode != 0 and "copy to b2:laqta/db failed" in done.stderr
    assert not old.exists(), "the cleanup of old dumps still ran"
    assert len(dumps_in(backups)) == 1 and dumps_in(backups)[0].endswith(".sql.gz.age")   # today's dump is there


@needs_bash
def test_backup_rejects_a_strange_rclone_remote_before_dumping(tmp_path):
    for bad in ("b2:x; rm -rf /", "no-colon", "b2:$(id)", "-evil:x"):
        done, backups = run_backup(tmp_path, stub_rclone(tmp_path), BACKUP_RCLONE_REMOTE=bad,
                                   BACKUP_RCLONE_ALLOW_PLAINTEXT="1")
        assert done.returncode != 0 and "BACKUP_RCLONE_REMOTE" in done.stderr, bad
        assert not (tmp_path / "dump_args.txt").exists()


@needs_bash
def test_backup_cleanup_covers_encrypted_dumps_and_stale_parts_but_only_this_databases(tmp_path):
    bindir = stub_dump(tmp_path)
    backups = tmp_path / "dumps"
    backups.mkdir()
    now = time.time()
    victims = ["automation_db_2020-01-01_000000.sql.gz", "automation_db_2020-01-01_000001.sql.gz.age",
               "automation_db_2020-01-01_000002.sql.gz.age.part"]
    survivors = ["automation_db_2020-01-02_000000.sql.gz.age", "automation_db_notes.sql.gz",
                 "automation_db_2020-01-01_000000.sql", "another_db_2020-01-01_000000.sql.gz.age"]
    for name in victims + survivors:
        (backups / name).write_bytes(b"x")
        age_days = 13 if name == survivors[0] else 40
        os.utime(backups / name, (now - age_days * 86400,) * 2)
    done, _ = run_backup(tmp_path, bindir)
    assert done.returncode == 0, done.stderr
    assert not any((backups / n).exists() for n in victims)
    assert all((backups / n).exists() for n in survivors)


@needs_bash
def test_backup_prefix_database_name_does_not_delete_a_longer_named_database(tmp_path):
    bindir = stub_dump(tmp_path)
    backups = tmp_path / "dumps"
    backups.mkdir()
    longer = backups / "automation_db_2020-01-01_000000.sql.gz"
    longer.write_bytes(b"x")
    os.utime(longer, (time.time() - 40 * 86400,) * 2)
    done, _ = run_backup(tmp_path, bindir, DB_NAME="automation")
    assert done.returncode == 0, done.stderr
    assert longer.exists(), "DB_NAME=automation must not match automation_db_*"


def test_backup_settings_file_template_documents_the_new_keys():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "#BACKUP_AGE_RECIPIENT=age1" in text and "#BACKUP_RCLONE_REMOTE=" in text


# ------------------------------------------------------------------ install.sh: how the dashboard is reached
@needs_bash
@needs_plain_path
def test_install_refuses_to_run_without_a_domain_or_local_only():
    """Audit item 1: the error says exactly what to do, and nothing was planned or changed."""
    done = run_install("--dry-run", str(APP))
    assert done.returncode == 1 and done.stdout == ""
    err = done.stderr
    assert "refusing to install" in err and "ONE shared login" in err
    assert f"install.sh {APP} --domain dash.example.com" in err
    assert "certbot certonly --webroot -w /var/www/letsencrypt -d dash.example.com" in err
    assert f"install.sh {APP} --local-only" in err and "ssh -L 8080:127.0.0.1:8080" in err
    assert "docs/deploy_ubuntu.md" in err


@needs_bash
@needs_plain_path
@pytest.mark.parametrize("bad", ["1.2.3.4", "dash", "_", "-bad.example.com", "dash.example.com;reboot", "a b.example.com"])
def test_install_domain_must_be_a_real_host_name(bad):
    done = run_install("--dry-run", str(APP), "--domain", bad)
    # a name with a character nginx must never see is refused by the general check, an IP or a bare word by this one
    assert done.returncode != 0 and done.stdout == ""
    assert "--domain needs a real host name" in done.stderr or "invalid --server-name" in done.stderr


@needs_bash
@needs_plain_path
def test_install_accepts_a_domain_the_old_server_name_option_and_local_only_together():
    for args in (["--domain", "Dash-1.example.co.uk"], ["--server-name", "dash.example.com"],
                 ["--local-only", "--domain", "dash.example.com"], ["--local-only"]):
        done = run_install("--dry-run", str(APP), *args)
        assert done.returncode in (0, 2), (args, done.stderr)


@needs_bash
@needs_plain_path
def test_install_adds_age_and_logrotate_always_and_fail2ban_and_rclone_only_when_asked():
    plain = run_install("--dry-run", str(APP), "--local-only").stdout
    install_line = next(line for line in plain.splitlines() if line.startswith("+ apt-get install"))
    assert " age " in install_line + " " and "logrotate" in install_line
    assert "fail2ban" not in plain and "rclone" not in plain
    both = run_install("--dry-run", str(APP), "--domain", "dash.example.com", "--with-fail2ban", "--with-rclone").stdout
    install_line = next(line for line in both.splitlines() if line.startswith("+ apt-get install"))
    assert "fail2ban" in install_line and "rclone" in install_line
    assert "render laqta-fail2ban.conf -> /etc/fail2ban/jail.d/laqta.conf" in both
    assert "systemctl reload fail2ban" in both and "fail2ban-client status laqta-nginx-auth" in both


@needs_bash
@needs_plain_path
def test_install_fail2ban_makes_no_sense_with_local_only():
    done = run_install("--dry-run", str(APP), "--local-only", "--with-fail2ban")
    assert done.returncode != 0 and "--with-fail2ban" in done.stderr and "127.0.0.1" in done.stderr


@needs_bash
@needs_plain_path
def test_install_says_what_is_missing_before_a_certificate_exists_and_nothing_once_it_does(tmp_path):
    before = run_install("--dry-run", str(APP), "--domain", "dash.example.com").stdout
    assert "NO certificate yet" in before and "127.0.0.1:8080 only" in before
    assert "certbot certonly --webroot -w /var/www/letsencrypt -d dash.example.com" in before
    folder = tmp_path / "live" / "dash.example.com"
    folder.mkdir(parents=True)
    (folder / "fullchain.pem").write_text("cert")
    (folder / "privkey.pem").write_text("key")
    after = run_install("--dry-run", str(APP), "--domain", "dash.example.com",
                        env={"LAQTA_CERT_ROOT": str(tmp_path / "live")}).stdout
    assert "NO certificate yet" not in after and "HTTPS is on for dash.example.com" in after


# ------------------------------------------------------------------ install.sh: production settings of dashboard/.env
def fake_app(tmp_path, env_text):
    """A folder install.sh accepts as the clone (requirements.txt, dashboard/artisan) with the given dashboard/.env."""
    app = tmp_path / "app"
    (app / "dashboard").mkdir(parents=True)
    (app / "requirements.txt").write_text("")
    (app / "dashboard" / "artisan").write_text("")
    if env_text is not None:
        (app / "dashboard" / ".env").write_text(env_text)
    return app


def https_env(tmp_path):
    folder = tmp_path / "live" / "dash.example.com"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fullchain.pem").write_text("cert")
    (folder / "privkey.pem").write_text("key")
    return {"LAQTA_CERT_ROOT": str(tmp_path / "live")}


@needs_bash
@pytest.mark.parametrize("line,message", [
    ("APP_DEBUG=true", "APP_DEBUG is on"),
    ("APP_DEBUG=TRUE", "APP_DEBUG is on"),
    ('APP_DEBUG="1"', "APP_DEBUG is on"),
    ("APP_DEBUG=true   # only for a minute", "APP_DEBUG is on"),
    ("APP_ENV=local", "APP_ENV is 'local'"),
    ("APP_ENV=staging # not yet", "APP_ENV is 'staging'"),
])
def test_install_refuses_an_unsafe_dashboard_env_before_changing_anything(tmp_path, line, message):
    app = fake_app(tmp_path, f"APP_KEY=base64:abc\n{line}\n")
    done = run_install("--dry-run", str(app), "--local-only")
    assert done.returncode == 1 and done.stdout == "", done.stdout
    assert message in done.stderr and "Nothing was changed" in done.stderr
    assert f"{app}/dashboard/.env" in done.stderr and "run this command again" in done.stderr


@needs_bash
def test_install_lists_every_unsafe_setting_at_once(tmp_path):
    app = fake_app(tmp_path, "APP_DEBUG=true\nAPP_ENV=local\nSESSION_SECURE_COOKIE=false\n")
    done = run_install("--dry-run", str(app), "--domain", "dash.example.com", env=https_env(tmp_path))
    assert done.returncode == 1
    for message in ("APP_DEBUG is on", "APP_ENV is 'local'", "SESSION_SECURE_COOKIE is 'false'"):
        assert message in done.stderr


@needs_bash
def test_install_wants_a_secure_cookie_on_https_but_not_for_the_plain_http_tunnel(tmp_path):
    app = fake_app(tmp_path, "APP_ENV=production\nAPP_DEBUG=false\nSESSION_SECURE_COOKIE=false\n")
    https = run_install("--dry-run", str(app), "--domain", "dash.example.com", env=https_env(tmp_path))
    assert https.returncode == 1 and "SESSION_SECURE_COOKIE" in https.stderr
    tunnel = run_install("--dry-run", str(app), "--local-only")                 # http://127.0.0.1:8080: a Secure cookie would break it
    assert tunnel.returncode in (0, 2), tunnel.stderr
    no_cert = run_install("--dry-run", str(app), "--domain", "dash.example.com")   # no certificate yet: still plain http on loopback
    assert no_cert.returncode in (0, 2), no_cert.stderr


@needs_bash
def test_install_accepts_a_safe_env_and_bakes_the_production_values_into_the_cached_config(tmp_path):
    for text in ("", 'APP_ENV="production"\nAPP_DEBUG="false"\n', "APP_ENV=Production\nAPP_DEBUG=false # fine\n"):
        app = fake_app(tmp_path / str(abs(hash(text))), text)
        done = run_install("--dry-run", str(app), "--local-only")
        assert done.returncode in (0, 2), done.stderr
        line = next(l for l in done.stdout.splitlines() if "config:cache" in l)
        assert "env APP_DEBUG=false APP_ENV=production " in line and "SESSION_SECURE_COOKIE" not in line
    https = run_install("--dry-run", str(fake_app(tmp_path / "h", "APP_KEY=base64:abc\n")), "--domain", "dash.example.com",
                        env=https_env(tmp_path))
    line = next(l for l in https.stdout.splitlines() if "config:cache" in l)
    assert "APP_DEBUG=false APP_ENV=production SESSION_SECURE_COOKIE=true" in line


def test_install_never_edits_the_env_file_to_enforce_production_values():
    code = "\n".join(l for l in (DEPLOY / "install.sh").read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))
    assert not re.search(r"sed\s+-i", code) and "APP_DEBUG=false" in code and "SESSION_SECURE_COOKIE=true" in code


def test_dashboard_env_template_is_production_safe_and_holds_no_secret():
    text = (DEPLOY / "dashboard.env.example").read_text(encoding="utf-8")
    keys = dict(line.split("=", 1) for line in text.splitlines() if re.match(r"^[A-Z_]+=", line))
    assert keys["APP_ENV"] == "production" and keys["APP_DEBUG"] == "false"
    assert keys["LOG_STACK"] == "daily" and keys["LOG_DAILY_DAYS"] == "14" and keys["LOG_LEVEL"] == "warning"
    assert keys["SESSION_SECURE_COOKIE"] == "true" and keys["SESSION_DRIVER"] == "file"
    assert keys["APP_KEY"] == "" and keys["DB_PASSWORD"] == ""                     # filled in on the server, never here
    assert keys["DB_USERNAME"] == "laqta_app" and keys["DB_HOST"] == "127.0.0.1"
    assert not re.search(r"(?i)(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|BEGIN PRIVATE KEY|base64:[A-Za-z0-9+/]{30,})", text)


# ------------------------------------------------------------------ memory limits, the flush timer
@needs_bash
@needs_plain_path
@pytest.mark.parametrize("ram,extra,expected", [
    ("4096", [], "2867M"),                                      # 70% of the RAM
    ("4096", ["--with-birefnet"], "3276M"),                     # 80% with BiRefNet
    ("16384", [], "11468M"),
    ("1024", [], "1024M"),                                      # never below 1 GB
])
def test_install_sizes_the_memory_limit_from_the_ram_it_finds(tmp_path, ram, extra, expected):
    out = install_render(tmp_path, "--local-only", *extra, env={"LAQTA_RAM_MB": ram})
    for name in PYTHON_SERVICES:
        text = (out / name).read_text(encoding="utf-8")
        assert f"MemoryMax={expected}\n" in text, name


@needs_bash
@needs_plain_path
def test_install_memory_limit_can_be_overridden_and_is_validated(tmp_path):
    out = install_render(tmp_path, "--local-only", env={"LAQTA_RAM_MB": "4096", "LAQTA_MEMORY_MAX": "3G"})
    assert "MemoryMax=3G\n" in (out / "laqta-nightly.service").read_text(encoding="utf-8")
    for bad in ("lots", "3 G", "3G; reboot", "150%%%"):
        done = run_install("--dry-run", str(APP), "--local-only", env={"LAQTA_MEMORY_MAX": bad})
        assert done.returncode != 0 and "LAQTA_MEMORY_MAX" in done.stderr, bad


@needs_bash
@needs_plain_path
def test_install_warns_when_birefnet_will_not_fit_and_when_the_ram_is_unknown():
    small = run_install("--dry-run", str(APP), "--local-only", "--with-birefnet", env={"LAQTA_RAM_MB": "2048"})
    assert "BiRefNet needs about 6 GB" in small.stderr and "--lite" in small.stderr
    lite = run_install("--dry-run", str(APP), "--local-only", "--with-birefnet", "--lite", env={"LAQTA_RAM_MB": "3072"})
    assert "BiRefNet needs about 4 GB" in lite.stderr
    roomy = run_install("--dry-run", str(APP), "--local-only", "--with-birefnet", env={"LAQTA_RAM_MB": "8192"})
    assert "BiRefNet needs" not in roomy.stderr
    unknown = run_install("--dry-run", str(APP), "--local-only", env={"LAQTA_RAM_MB": "plenty"})
    assert "could not detect the RAM size" in unknown.stderr and unknown.returncode in (0, 2)


@pytest.mark.parametrize("name", PYTHON_SERVICES)
def test_python_services_have_a_memory_cap_and_die_alone_when_they_pass_it(name):
    unit = rendered_unit(name, memory="2867M")
    assert values(unit, "Service", "MemoryMax") == ["2867M"]
    assert values(unit, "Service", "OOMPolicy") == ["kill"]
    assert int(values(unit, "Service", "OOMScoreAdjust")[0]) > 0        # the first victim of the kernel, never MariaDB


def test_no_unit_points_to_the_removed_failure_alert_unit():
    for name in UNITS:
        if name.endswith(".service"):
            assert not values(rendered_unit(name), "Unit", "OnFailure"), name
    assert not (DEPLOY / "laqta-alert@.service").exists() and not (REPO / "scripts" / "unit_alert.py").exists()


SHUTDOWN_GRACE_S = 45        # the graceful-stop wait of the worker and run_nightly (their SIGTERM handler), seconds


def seconds(value):
    """systemd time such as 120, 120s or 2min -> seconds."""
    m = re.fullmatch(r"(\d+)\s*(s|sec|min|m|h)?", value.strip())
    return int(m.group(1)) * {None: 1, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600}[m.group(2)]


@pytest.mark.parametrize("name", ["laqta-nightly.service", "laqta-sync-worker.service", "laqta-run.service"])
def test_long_running_units_get_time_to_stop_gracefully_and_a_clean_stop_is_not_a_failure(name):
    """SIGTERM starts a graceful shutdown (up to SHUTDOWN_GRACE_S, then the report) and the process exits with code 3,
    'stopped on purpose': systemd must wait longer than the grace before SIGKILL, and exit 3 must count as success so
    that a stop is not a unit failure."""
    unit = rendered_unit(name)
    assert values(unit, "Service", "KillSignal") == ["SIGTERM"]
    stop_wait = seconds(values(unit, "Service", "TimeoutStopSec")[0])
    assert stop_wait >= 90 and stop_wait > 2 * SHUTDOWN_GRACE_S
    assert "3" in values(unit, "Service", "SuccessExitStatus")[0].split()
    assert not values(unit, "Service", "KillMode") or values(unit, "Service", "KillMode") == ["control-group"]   # stop reaches every process


def test_the_dashboard_run_unit_still_accepts_the_stop_buttons_kill_and_a_plain_stop():
    codes = values(rendered_unit("laqta-run.service"), "Service", "SuccessExitStatus")[0].split()
    assert {"3", "KILL", "TERM"} <= set(codes)


def test_outbox_flush_runs_the_flush_script_every_two_minutes_without_overlapping():
    unit = rendered_unit("laqta-outbox-flush.service")
    assert values(unit, "Service", "Type") == ["oneshot"]       # a running oneshot ignores the next timer start
    assert values(unit, "Service", "User") == ["laqta"] and values(unit, "Service", "WorkingDirectory") == [SAMPLE_APP]
    assert values(unit, "Service", "ExecStart") == [f"{SAMPLE_APP}/.venv/bin/python -X utf8 {SAMPLE_APP}/scripts/flush_sheets_sync.py"]
    assert (REPO / "scripts" / "flush_sheets_sync.py").is_file()
    assert values(unit, "Service", "TimeoutStartSec")           # a hung Google call must not block every later flush
    assert "[Install]" not in render((DEPLOY / "laqta-outbox-flush.service").read_text(encoding="utf-8"))
    timer = rendered_unit("laqta-outbox-flush.timer")
    assert values(timer, "Timer", "OnUnitActiveSec") == ["2min"] and values(timer, "Timer", "OnBootSec")
    assert values(timer, "Timer", "Unit") == ["laqta-outbox-flush.service"]
    assert values(timer, "Install", "WantedBy") == ["timers.target"]


def test_run_launcher_units_watch_the_file_the_dashboard_writes():
    path_unit = rendered_unit("laqta-run.path")
    assert values(path_unit, "Path", "PathExists") == [f"{SAMPLE_APP}/temp/run_request.json"]
    assert values(path_unit, "Path", "Unit") == ["laqta-run.service"]
    assert values(path_unit, "Install", "WantedBy") == ["multi-user.target"]
    service = rendered_unit("laqta-run.service")
    assert values(service, "Service", "Type") == ["oneshot"] and values(service, "Service", "User") == ["laqta"]
    assert values(service, "Service", "ExecStart") == [f"/bin/bash {SAMPLE_APP}/deploy/ubuntu/run-launcher.sh {SAMPLE_APP}"]
    assert "KILL" in values(service, "Service", "SuccessExitStatus")[0].split()   # the stop button kills the worker: normal
    assert "[Install]" not in render((DEPLOY / "laqta-run.service").read_text(encoding="utf-8"))   # started by the path unit only
    # the three places that must agree on the file name
    assert "run_request.json" in (REPO / "dashboard" / "app" / "Services" / "RunLauncher.php").read_text(encoding="utf-8")
    assert "run_request.json" in (DEPLOY / "run-launcher.sh").read_text(encoding="utf-8")


@needs_bash
@needs_plain_path
def test_install_always_switches_the_run_launcher_on_and_the_flush_timer_with_the_other_timers():
    plain = run_install("--dry-run", str(APP), "--local-only").stdout
    assert "systemctl enable --now laqta-run.path" in plain
    assert "systemctl enable --now laqta-nightly.timer laqta-backup.timer laqta-outbox-flush.timer" in plain   # the NOT enabled hint
    enabled = run_install("--dry-run", str(APP), "--local-only", "--enable-units").stdout
    assert "+ systemctl enable --now laqta-backup.timer laqta-nightly.timer laqta-outbox-flush.timer" in enabled
    assert "render laqta-logrotate -> /etc/logrotate.d/laqta" in enabled and "render laqta-alert@" not in enabled
    assert "+ rm -f /etc/systemd/system/laqta-alert@.service" in enabled       # a server installed before drops the old unit


# ------------------------------------------------------------------ the dashboard run launcher script
def launcher_app(tmp_path, enqueue_fails=False):
    """A fake clone: .venv/bin/python records its arguments, temp/ is where the request and the log live."""
    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / "temp").mkdir()
    calls = tmp_path / "calls.txt"
    python = app / ".venv" / "bin" / "python"
    python.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{calls}"\n'
        'echo "output of: $*"\n'
        + ('case "$*" in *--enqueue*) exit 1;; esac\n' if enqueue_fails else ""))
    python.chmod(0o755)
    return app, calls


def run_launcher(app, *extra, env=None):
    return subprocess.run([BASH, str(DEPLOY / "run-launcher.sh"), str(app), *extra], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=60)


@needs_bash
def test_launcher_script_takes_the_request_and_runs_enqueue_then_the_worker_into_pipeline_log(tmp_path):
    app, calls = launcher_app(tmp_path)
    request = app / "temp" / "run_request.json"
    request.write_text("{}")
    done = run_launcher(app)
    assert done.returncode == 0, done.stderr
    assert not request.exists() and (app / "temp" / "run_request.json.taken").exists()   # taken with one mv
    lines = calls.read_text().splitlines()
    assert lines == [f"{app}/main.py --enqueue", f"-u {app}/main.py --worker --trigger=dashboard"]
    log = (app / "temp" / "pipeline.log").read_text()
    assert "output of: " in log and "--worker --trigger=dashboard" in log and done.stdout == ""   # all output in the log


@needs_bash
def test_launcher_script_does_nothing_when_there_is_no_request_or_it_was_taken_already(tmp_path):
    app, calls = launcher_app(tmp_path)
    done = run_launcher(app)
    assert done.returncode == 0 and "no run request" in done.stdout and not calls.exists()
    assert not (app / "temp" / "pipeline.log").exists()


@needs_bash
def test_launcher_script_ignores_a_stale_request_left_over_from_before_a_reboot(tmp_path):
    app, calls = launcher_app(tmp_path)
    request = app / "temp" / "run_request.json"
    request.write_text("{}")
    os.utime(request, (time.time() - 3600,) * 2)
    done = run_launcher(app)
    assert done.returncode == 0 and "stale run request" in done.stdout and not calls.exists()
    assert not request.exists()                                    # consumed, so the path unit does not loop


@needs_bash
def test_launcher_script_does_not_start_the_worker_when_the_enqueue_fails(tmp_path):
    app, calls = launcher_app(tmp_path, enqueue_fails=True)
    (app / "temp" / "run_request.json").write_text("{}")
    done = run_launcher(app)
    assert done.returncode == 1
    assert calls.read_text().splitlines() == [f"{app}/main.py --enqueue"]


@needs_bash
def test_the_worker_keeps_the_launchers_pid_so_the_stop_button_still_reaches_it(tmp_path):
    """The dashboard stops a run with `kill -9 <pid in temp/pipeline.lock>` (ApiController::terminateWorker). The lock
    holds the worker's own pid, so the launcher must exec the worker (same pid as the unit's main process): killing it
    is then what systemd sees as 'KILL', which laqta-run.service lists in SuccessExitStatus (no false failure)."""
    app, calls = launcher_app(tmp_path)
    worker_pid = tmp_path / "worker.pid"
    python = app / ".venv" / "bin" / "python"
    python.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{calls}"\n'
        f'case "$*" in *--worker*) echo $$ > "{worker_pid}"; sleep 60;; esac\n')
    (app / "temp" / "run_request.json").write_text("{}")
    proc = subprocess.Popen([BASH, str(DEPLOY / "run-launcher.sh"), str(app)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if worker_pid.exists() and worker_pid.read_text().strip():
                break
            time.sleep(0.05)
        assert int(worker_pid.read_text()) == proc.pid, "the worker must be the launcher process itself (exec)"
        os.kill(proc.pid, 9)                                       # exactly what terminateWorker runs: kill -9 <pid>
        assert proc.wait(timeout=10) == -9                         # systemd calls this result "KILL"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert "KILL" in values(rendered_unit("laqta-run.service"), "Service", "SuccessExitStatus")[0].split()


@needs_bash
def test_launcher_script_never_reads_the_request_content(tmp_path):
    app, calls = launcher_app(tmp_path)
    (app / "temp" / "run_request.json").write_text('$(touch /tmp/laqta-pwned); `id`; --worker --evil')
    done = run_launcher(app)
    assert done.returncode == 0
    assert calls.read_text().splitlines() == [f"{app}/main.py --enqueue", f"-u {app}/main.py --worker --trigger=dashboard"]
    assert not Path("/tmp/laqta-pwned").exists()


@needs_bash
@pytest.mark.parametrize("bad", ["relative/path", "/opt/laqta; reboot", "/opt/la qta"])
def test_launcher_script_refuses_a_strange_app_dir(bad):
    done = run_launcher(bad)
    assert done.returncode == 64 and "APP_DIR must be an absolute plain path" in done.stderr


@needs_bash
def test_launcher_script_needs_the_venv(tmp_path):
    app, calls = launcher_app(tmp_path)
    (app / ".venv" / "bin" / "python").unlink()
    (app / "temp" / "run_request.json").write_text("{}")
    done = run_launcher(app)
    assert done.returncode == 1 and "run install.sh" in done.stderr


# ------------------------------------------------------------------ nginx: request limits and /healthz
def locations(text):
    """[(header, block text)] of every `location` inside the dashboard server of an nginx file."""
    server = server_blocks(text)[0]
    found = []
    for m in re.finditer(r"(?m)^    location\s+([^{]+?)\s*\{", server):
        depth, i = 0, m.end() - 1
        while True:
            depth += 1 if server[i] == "{" else -1 if server[i] == "}" else 0
            i += 1
            if depth == 0:
                break
        found.append((m.group(1), server[m.start():i]))
    return found


def test_nginx_site_throttles_requests_and_every_authenticated_location_inherits_it():
    """Audit item 1: limit_req on the authenticated locations. nginx inherits it to every location that sets none of its own."""
    text = re.sub(r"(?m)^\s*#.*$", "", site())
    zones = {name: (size, rate) for name, size, rate in re.findall(r"limit_req_zone\s+\S+\s+zone=(\w+):(\w+)\s+rate=(\S+);", text)}
    assert set(zones) == {"laqta_req", "laqta_noauth"} and "limit_req_status 429;" in text
    server = server_blocks(text)[0]
    head = server[:server.index("location ")]
    assert re.search(r"limit_req\s+zone=laqta_req\s+burst=\d+\s+nodelay;", head)       # the whole site
    assert re.search(r"limit_req\s+zone=laqta_noauth\s+burst=\d+\s+nodelay;", head)    # requests with no password at all
    own = {header: re.findall(r"limit_req\s+zone=(\w+)", body) for header, body in locations(text)}
    assert [h for h, zs in own.items() if zs] == ["= /healthz"]                         # only the monitor path sets its own
    assert own["= /healthz"] == ["laqta_req"]                                           # a monitor sends no password: no noauth zone
    used = set(re.findall(r"limit_req\s+zone=(\w+)", text))
    assert used <= set(zones)
    # the second zone only counts requests that carry no Authorization header (the key is empty for the others)
    assert re.search(r'map\s+\$http_authorization\s+\$laqta_noauth_key\s*\{\s*default\s+"";\s*""\s+\$binary_remote_addr;', text)
    assert "limit_req_zone $laqta_noauth_key zone=laqta_noauth" in text and "zone=laqta_req" in text
    assert "$binary_remote_addr zone=laqta_req" in text


def test_healthz_location_is_open_only_to_loopback_and_the_monitor_addresses():
    text = site(monitor="        allow 203.0.113.7;\n        allow 2001:db8::/32;")
    loc = dict(locations(re.sub(r"(?m)^\s*#.*$", "", text)))["= /healthz"]
    assert "auth_basic off;" in loc
    allows = re.findall(r"allow\s+(\S+);", loc)
    assert allows == ["127.0.0.1", "::1", "203.0.113.7", "2001:db8::/32"]
    assert loc.index("deny all;") > loc.rindex("allow ")                                  # every allow comes before the deny
    assert "fastcgi_pass unix:/run/php/laqta.sock;" in loc
    assert "SCRIPT_FILENAME $realpath_root/index.php" in loc                              # only the front controller runs
    assert dict(locations(re.sub(r"(?m)^\s*#.*$", "", site())))["= /healthz"].count("allow ") == 2    # no monitor given


@needs_bash
@needs_plain_path
def test_install_renders_the_monitor_addresses_from_the_flag_and_from_the_environment(tmp_path):
    out = install_render(tmp_path, "--local-only", "--monitor-ip", "203.0.113.7", "--monitor-ip", "2001:db8::/32,198.51.100.0/24",
                         env={"LAQTA_MONITOR_IPS": "192.0.2.5"})
    loc = dict(locations(re.sub(r"(?m)^\s*#.*$", "", (out / "laqta.conf").read_text(encoding="utf-8"))))["= /healthz"]
    assert re.findall(r"allow\s+(\S+);", loc) == ["127.0.0.1", "::1", "192.0.2.5", "203.0.113.7", "2001:db8::/32", "198.51.100.0/24"]
    assert "@" not in loc


@needs_bash
@needs_plain_path
@pytest.mark.parametrize("bad", ["300.1.1.1", "1.2.3", "1.2.3.4/33", "evil", "1.2.3.4; deny all", "::g", "2001:db8::/129",
                                 "1.2.3.4/", "$(id)"])
def test_install_rejects_a_monitor_address_that_could_change_the_nginx_config(bad):
    done = run_install("--dry-run", str(APP), "--local-only", "--monitor-ip", bad)
    assert done.returncode != 0 and "invalid monitor address" in done.stderr and done.stdout == ""


@needs_bash
@needs_plain_path
@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx is not installed")
@pytest.mark.parametrize("args", [["--local-only", "--monitor-ip", "203.0.113.7"], ["--domain", "dash.example.com"]])
def test_nginx_accepts_the_installed_site_with_limits_healthz_and_the_certbot_only_port_80(tmp_path, args):
    out = install_render(tmp_path, *args)
    text = (out / "laqta.conf").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^\s*listen \[::\]:\d+[^;]*;\n", "", text)         # test boxes without IPv6 cannot bind [::]
    text = _unprivileged_ports(text)
    text = re.sub(r"(access_log|error_log)\s+\S+;", lambda m: f"{m.group(1)} {tmp_path}/{m.group(1)}.log;", text)
    text = text.replace("root /var/www/letsencrypt;", f"root {tmp_path};")
    (tmp_path / "site.conf").write_text(text)
    (tmp_path / "fastcgi_params").write_text("fastcgi_param QUERY_STRING $query_string;\n")
    (tmp_path / "nginx.conf").write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/main.log;\nevents {{}}\n"
        f"http {{\n    access_log off;\n    include {tmp_path}/site.conf;\n}}\n")
    done = subprocess.run(["nginx", "-t", "-c", str(tmp_path / "nginx.conf"), "-p", str(tmp_path)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


@contextlib.contextmanager
def live_nginx(tmp_path, password, monitor="        allow 203.0.113.7;", drop_loopback=False):
    """The real site file under a real nginx on a free high port: static files nowhere, php-fpm absent (a request that
    gets through authentication and the limits ends as 502 because the socket does not exist)."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    text = site(listen=f"listen 127.0.0.1:{port};", server_name="_", monitor=monitor)
    text = re.sub(r"(access_log|error_log)\s+\S+;", lambda m: f"{m.group(1)} {tmp_path}/{m.group(1)}.log;", text)
    text = text.replace("root /var/www/letsencrypt;", f"root {tmp_path};")
    users = tmp_path / "users"
    users.write_text("admin:{PLAIN}" + password + "\n")
    text = text.replace("/etc/nginx/laqta.htpasswd", str(users))
    if drop_loopback:
        text = text.replace("allow 127.0.0.1;", "")
    (tmp_path / "site.conf").write_text(text)
    (tmp_path / "fastcgi_params").write_text("fastcgi_param QUERY_STRING $query_string;\n")
    temp_dirs = "".join(f"    {d}_temp_path {tmp_path}/{d}_tmp;\n" for d in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
    as_root = "user root;\n" if os.geteuid() == 0 else ""       # a root master would run the workers as nobody: they could not read tmp
    (tmp_path / "nginx.conf").write_text(
        f"{as_root}pid {tmp_path}/nginx.pid;\ndaemon off;\nerror_log {tmp_path}/main.log;\nevents {{}}\n"
        f"http {{\n    access_log off;\n{temp_dirs}    include {tmp_path}/site.conf;\n}}\n")
    proc = subprocess.Popen(["nginx", "-c", str(tmp_path / "nginx.conf"), "-p", str(tmp_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise AssertionError("nginx did not start: " + proc.stderr.read())
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("nginx did not listen")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def http_status(port, path, user=None, password=None):
    headers = {}
    if user is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


needs_live_nginx = pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx is not installed")


@needs_live_nginx
def test_live_nginx_throttles_a_flood_of_requests_with_no_password(tmp_path):
    good = _throwaway("live1")
    with live_nginx(tmp_path, good) as port:
        codes = [http_status(port, "/") for _ in range(60)]
    assert codes[:10] == [401] * 10, codes                         # a browser's first, password-less request is always answered
    assert 429 in codes and set(codes) <= {401, 429}


@needs_live_nginx
def test_live_nginx_throttles_a_flood_of_wrong_passwords_and_lets_the_address_back_in_a_moment_later(tmp_path):
    good, wrong = _throwaway("live2"), _throwaway("wrong")
    with live_nginx(tmp_path, good) as port:
        guesses = [http_status(port, "/", "admin", wrong) for _ in range(250)]
        time.sleep(1.5)                                            # the limit is per address and refills at 10 requests a second
        later = [http_status(port, "/api/overview", "admin", good) for _ in range(3)]
    assert guesses[0] == 401 and 429 in guesses and set(guesses) <= {401, 429}
    assert guesses.count(401) < 120, "the guesses must be cut off, not all answered"
    assert later == [502] * 3                                      # through authentication and the limit again


@needs_live_nginx
def test_live_nginx_normal_use_is_not_throttled_and_only_the_front_controller_is_reachable(tmp_path):
    good = _throwaway("live3")
    with live_nginx(tmp_path, good) as port:
        busy = [http_status(port, "/api/batch-status", "admin", good) for _ in range(40)]     # a page polling and loading images
        assert busy == [502] * 40                                  # through auth and limits: only php-fpm (absent here) is missing
        assert http_status(port, "/index.php", "admin", good) == 502
        for blocked in ("/.env", "/.git/config", "/storage/logs/laravel.log", "/vendor/autoload.php", "/x.json", "/backup.sql",
                        "/admin.php", "/public/index.php.bak"):
            assert http_status(port, blocked, "admin", good) == 404, blocked
        assert http_status(port, "/.well-known/acme-challenge/token", ) == 404     # open without a password, serves only files


@needs_live_nginx
def test_live_nginx_healthz_needs_no_password_but_only_for_the_allowed_addresses(tmp_path):
    good = _throwaway("live4")
    with live_nginx(tmp_path, good) as port:
        assert http_status(port, "/healthz") == 502                # no 401: the check is open, php-fpm is just absent here
        assert http_status(port, "/") == 401                       # everything else still asks for the password
    other = tmp_path / "other"
    other.mkdir()
    with live_nginx(other, good, drop_loopback=True) as port:      # this machine is not in the list: refused
        assert http_status(port, "/healthz") == 403
        assert http_status(port, "/healthz", "admin", good) == 403   # a password does not open it either


# ------------------------------------------------------------------ fail2ban jail
def test_fail2ban_jail_watches_the_site_log_with_the_stock_nginx_auth_filter():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string((DEPLOY / "laqta-fail2ban.conf").read_text(encoding="utf-8"))
    assert parser.sections() == ["laqta-nginx-auth"]
    jail = parser["laqta-nginx-auth"]
    assert jail["enabled"] == "true" and jail["filter"] == "nginx-http-auth"
    error_log = re.search(r"^\s*error_log\s+(\S+);", site(), re.M).group(1)
    assert jail["logpath"] == error_log == "/var/log/nginx/laqta.error.log"      # the log the site writes
    assert int(jail["maxretry"]) == 5 and jail["findtime"] == "10m" and jail["bantime"] == "1h"
    assert "http" in jail["port"] and "https" in jail["port"]
    assert "@" not in re.sub(r"(?m)^\s*#.*$", "", (DEPLOY / "laqta-fail2ban.conf").read_text(encoding="utf-8"))


@needs_bash
@needs_plain_path
def test_install_renders_the_fail2ban_jail_only_when_asked(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    without = {p.name for p in install_render(tmp_path / "a", "--domain", "dash.example.com").iterdir()}
    with_jail = {p.name for p in install_render(tmp_path / "b", "--domain", "dash.example.com", "--with-fail2ban").iterdir()}
    assert "laqta-fail2ban.conf" not in without and "laqta-fail2ban.conf" in with_jail


NGINX_ERRORS = """\
2026/10/06 03:10:01 [error] 812#812: *41 user "admin": password mismatch, client: 203.0.113.9, server: dash.example.com, request: "GET / HTTP/1.1", host: "dash.example.com"
2026/10/06 03:10:02 [error] 812#812: *42 user "root" was not found in "/etc/nginx/laqta.htpasswd", client: 203.0.113.9, server: dash.example.com, request: "GET /api/overview HTTP/1.1", host: "dash.example.com"
2026/10/06 03:10:03 [error] 812#812: *43 user "admin": password mismatch, client: 198.51.100.7, server: dash.example.com, request: "GET /catalog HTTP/1.1", host: "dash.example.com", referrer: "https://dash.example.com/"
2026/10/06 03:10:04 [error] 812#812: *44 no user/password was provided for basic authentication, client: 192.0.2.50, server: dash.example.com, request: "GET / HTTP/1.1", host: "dash.example.com"
2026/10/06 03:10:05 [warn] 812#812: *45 limiting requests, excess: 60.500 by zone "laqta_req", client: 192.0.2.60, server: dash.example.com, request: "GET / HTTP/1.1", host: "dash.example.com"
2026/10/06 03:10:06 [error] 812#812: *46 connect() to unix:/run/php/laqta.sock failed (2: No such file or directory) while connecting to upstream, client: 192.0.2.70, server: dash.example.com, request: "GET /index.php HTTP/1.1", upstream: "fastcgi://unix:/run/php/laqta.sock:", host: "dash.example.com"
"""


@pytest.mark.skipif(shutil.which("fail2ban-regex") is None or not Path("/etc/fail2ban/filter.d/nginx-http-auth.conf").is_file(),
                    reason="fail2ban is not installed")
def test_fail2ban_filter_counts_wrong_passwords_and_nothing_else(tmp_path):
    sample = tmp_path / "laqta.error.log"
    sample.write_text(NGINX_ERRORS)
    done = subprocess.run(["fail2ban-regex", str(sample), "/etc/fail2ban/filter.d/nginx-http-auth.conf"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "3 matched, 3 missed" in done.stdout                    # 2 wrong passwords + 1 unknown user; not the plain 401,
    missed = done.stdout.split("Missed line(s):", 1)[1]            # not the rate limit, not a php-fpm outage
    for line_part in ("no user/password was provided", "limiting requests", "connect() to unix"):
        assert line_part in missed


# ------------------------------------------------------------------ php-fpm pool: uploads and the launcher marker
def php_size_mb(value):
    m = re.fullmatch(r"(\d+)([KMG]?)", value.strip(), re.I)
    return int(m.group(1)) * {"": 1 / 1048576, "K": 1 / 1024, "M": 1, "G": 1024}[m.group(2).upper()]


def test_fpm_pool_lets_a_manual_upload_through_as_wide_as_nginx_does():
    """Audit item 4: the PHP defaults (2M / 8M) were below nginx's 20m, so a manual image over 2 MB failed."""
    pool = render((DEPLOY / "laqta-fpm-pool.conf").read_text(encoding="utf-8"))
    upload = re.search(r"^php_admin_value\[upload_max_filesize\] = (\S+)$", pool, re.M).group(1)
    post = re.search(r"^php_admin_value\[post_max_size\] = (\S+)$", pool, re.M).group(1)
    assert (upload, post) == ("20M", "25M")
    nginx_limit = re.search(r"^\s*client_max_body_size\s+(\d+m);", site(), re.M).group(1)
    assert php_size_mb(nginx_limit) <= php_size_mb(upload) <= php_size_mb(post) and php_size_mb(upload) > 8
    assert re.search(r"(?m)^\s*client_max_body_size 20m;", site())


def test_fpm_pool_tells_the_dashboard_the_systemd_launcher_is_installed():
    pool = render((DEPLOY / "laqta-fpm-pool.conf").read_text(encoding="utf-8"))
    assert re.search(r"^env\[LAQTA_RUN_LAUNCHER\] = systemd$", pool, re.M)
    assert "clear_env = yes" in pool                                # so this line is the only way the variable gets there


# ------------------------------------------------------------------ logrotate drop-in
def render_logrotate(tmp_path, root):
    user, group = getpass.getuser(), grp.getgrgid(os.getgid()).gr_name
    text = render((DEPLOY / "laqta-logrotate").read_text(encoding="utf-8"), app_dir=str(root), user=user)
    if os.geteuid() != 0:
        # a non-root logrotate cannot switch user (setgroups needs root), and the test files are ours anyway
        return text.replace(f"    su {user} {user}\n", "")
    return text.replace(f"su {user} {user}", f"su {user} {group}")


def test_logrotate_covers_the_three_log_places_and_never_collides_with_the_dashboards_numbered_copies():
    text = render((DEPLOY / "laqta-logrotate").read_text(encoding="utf-8"))
    code = re.sub(r"(?m)^\s*#.*$", "", text)
    for path in (f"{SAMPLE_APP}/dashboard/storage/logs/laravel.log", f"{SAMPLE_APP}/temp/*.log", f"{SAMPLE_APP}/temp/nightly/*.log"):
        assert path in code, path
    assert not PLACEHOLDER.search(code)
    first, second = code.split("}", 1)[0], code.split("}", 1)[1]
    for directive in ("copytruncate", "dateext", "weekly", "maxsize 50M", "rotate 8", "compress", "missingok", "notifempty",
                      f"su laqta laqta"):
        assert directive in first, directive                       # dateext: names never clash with pipeline.log.1 .. .5
    assert "nightly" not in first and "nightly" in second
    assert "rotate 0" in second and "minage 60" in second and "copytruncate" not in second   # nightly logs: deleted at 60 days, never emptied


@pytest.mark.skipif(shutil.which("logrotate") is None, reason="logrotate is not installed")
def test_logrotate_accepts_the_drop_in_and_rotates_without_touching_the_dashboards_copies(tmp_path):
    root = tmp_path / "app"
    for sub in ("temp/nightly", "dashboard/storage/logs"):
        (root / sub).mkdir(parents=True)
    (root / "temp" / "pipeline.log").write_text("this run\n" * 50)
    (root / "temp" / "pipeline.log.1").write_text("the dashboard's own copy of the run before\n")
    (root / "dashboard" / "storage" / "logs" / "laravel.log").write_text("error\n" * 10)
    conf = tmp_path / "laqta.conf"
    conf.write_text(render_logrotate(tmp_path, root))
    conf.chmod(0o644)
    state = tmp_path / "state"
    checked = subprocess.run(["logrotate", "-d", "-s", str(state), str(conf)], capture_output=True, text=True)
    assert checked.returncode == 0 and "error:" not in checked.stderr.lower(), checked.stderr
    # force only the first block (the nightly block is a delete-by-age rule, tested below)
    first = tmp_path / "first.conf"
    first.write_text(conf.read_text().split("\n}\n", 1)[0] + "\n}\n")
    first.chmod(0o644)
    done = subprocess.run(["logrotate", "-f", "-s", str(state), str(first)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    names = sorted(p.name for p in (root / "temp").iterdir())
    dated = [n for n in names if re.fullmatch(r"pipeline\.log-\d{8}", n)]
    assert len(dated) == 1 and "pipeline.log" in names and (root / "temp" / "pipeline.log").stat().st_size == 0   # copied, emptied
    assert (root / "temp" / "pipeline.log.1").read_text().startswith("the dashboard's own copy")             # untouched
    assert (root / "temp" / dated[0]).read_text().count("this run") == 50
    assert any(re.fullmatch(r"laravel\.log-\d{8}", p.name) for p in (root / "dashboard" / "storage" / "logs").iterdir())


@pytest.mark.skipif(shutil.which("logrotate") is None, reason="logrotate is not installed")
def test_logrotate_deletes_nightly_logs_after_60_days_and_keeps_the_rest_unchanged(tmp_path):
    root = tmp_path / "app"
    nightly = root / "temp" / "nightly"
    nightly.mkdir(parents=True)
    now = time.time()
    for name, days in (("old", 80), ("mid", 40), ("new", 1)):
        path = nightly / f"nightly_{name}.log"
        path.write_text("night\n")
        os.utime(path, (now - days * 86400,) * 2)
    conf = tmp_path / "nightly.conf"
    text = render_logrotate(tmp_path, root)
    conf.write_text(text[text.index(f"{root}/temp/nightly/*.log"):])          # only the second block
    conf.chmod(0o644)
    longago = datetime.date.today() - datetime.timedelta(days=3)       # the state file says the logs were handled 3 days ago
    state = tmp_path / "state"
    state.write_text("logrotate state -- version 2\n" + "".join(
        f'"{nightly}/nightly_{n}.log" {longago.year}-{longago.month}-{longago.day}-0:0:0\n' for n in ("old", "mid", "new")))
    done = subprocess.run(["logrotate", "-s", str(state), str(conf)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in nightly.iterdir()) == ["nightly_mid.log", "nightly_new.log"]
    assert (nightly / "nightly_new.log").read_text() == "night\n"                # never emptied: the log viewer reads it


# ------------------------------------------------------------------ docs
def test_deploy_guide_is_arabic_and_covers_every_step():
    doc = REPO / "docs" / "deploy_ubuntu.md"
    text = doc.read_text(encoding="utf-8")
    assert len(re.findall(r"[؀-ۿ]", text)) > 1500
    # the basic-auth tool's name is assembled: next to another string it reads as a "passwd = value" pair to scanners
    for needle in ("git clone", "scp", "install.sh", "--with-birefnet", "--lite", "--gpu", "--local-only",
                   "certbot", "laqta-nightly.timer", "laqta-backup.timer", "server_check.py",
                   ".my.cnf", "git pull", "ssh -L", "Tailscale", "chmod 600", "htpass" + "wd"):
        assert needle in text, needle
    assert "git reset --hard" in text or "git checkout" in text      # the rollback section
    assert not re.search(r"(?i)(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|BEGIN PRIVATE KEY)", text)


def test_deploy_guide_covers_the_new_pieces_with_exact_commands():
    text = (REPO / "docs" / "deploy_ubuntu.md").read_text(encoding="utf-8")
    for needle in (
            # item 1: how the dashboard is reached, limits, fail2ban
            "--domain dash.example.com", "--local-only", "--with-fail2ban", "fail2ban-client status laqta-nginx-auth",
            "fail2ban-client set laqta-nginx-auth unbanip", "429",
            # item 2: the outbox flush
            "laqta-outbox-flush.timer", "journalctl -u laqta-outbox-flush -e", "DEAD",
            # item 3 and 4: logs, uploads
            "logrotate -d /etc/logrotate.d/laqta", "LOG_STACK=daily", "pipeline.log.1", "upload_max_filesize=20M", "post_max_size=25M",
            # item 5: memory
            "MemoryMax", "systemctl edit laqta-nightly", "LAQTA_MEMORY_MAX", "LAQTA_RAM_MB", "--lite",
            # item 7: /healthz and the two monitors
            "/healthz", "--monitor-ip", "UptimeRobot", "Uptime Kuma", "503", "curl -s --resolve dash.example.com:443:127.0.0.1",
            # item 8: the launcher
            "laqta-run.path", "systemctl status laqta-run.path", "run_request.json", "systemctl stop laqta-run",
            # item 9: production settings
            "dashboard.env.example", "APP_DEBUG=false", "SESSION_SECURE_COOKIE=true", "config:cache",
            # item 10: backups
            "BACKUP_AGE_RECIPIENT", "age-keygen -o laqta-backup.key", "age -d -i laqta-backup.key", "BACKUP_RCLONE_REMOTE",
            "BACKUP_RCLONE_ALLOW_PLAINTEXT", "rclone config", "--with-rclone"):
        assert needle in text, needle


def test_every_docs_section_the_scripts_point_to_exists_and_is_about_that_topic():
    text = (REPO / "docs" / "deploy_ubuntu.md").read_text(encoding="utf-8")
    headings = {int(m.group(1)): m.group(2) for m in re.finditer(r"(?m)^## (\d+)\. (.+)$", text)}
    assert sorted(headings) == list(range(1, 16))                   # numbered without gaps
    topics = {4: "install.sh", 6: "HTTPS", 7: "localhost", 8: "التايمرات", 9: "النسخ الاحتياطي", 11: "/healthz", 12: "السجلات"}
    for number, word in topics.items():
        assert word in headings[number], (number, headings[number])
    referenced = set()
    for script in sorted(DEPLOY.glob("*.sh")) + [REPO / "scripts" / "server_check.py"]:
        for m in re.finditer(r"section (\d+)", script.read_text(encoding="utf-8")):
            referenced.add(int(m.group(1)))
    assert referenced and referenced <= set(headings), referenced
    assert {4, 6, 7, 8, 9} <= referenced
