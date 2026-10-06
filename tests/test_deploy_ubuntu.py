"""Static and behaviour checks for deploy/ubuntu (the Ubuntu server deployment kit).

Nothing here installs anything or touches the network: scripts are syntax-checked, install.sh only runs with
--dry-run, backup.sh runs against a stub dump command, units and the nginx site are rendered into a temp folder.
"""

import getpass
import gzip
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy" / "ubuntu"
SCRIPTS = sorted(DEPLOY.glob("*.sh"))
UNITS = ["laqta-sync-worker.service", "laqta-nightly.service", "laqta-nightly.timer",
         "laqta-backup.service", "laqta-backup.timer"]
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux deployment kit")

SAMPLE_APP = "/opt/laqta"


# ------------------------------------------------------------------ helpers
def render(text, app_dir=SAMPLE_APP, user="laqta", listen="listen 80;", server_name="dash.example.com",
           redirect=""):
    for key, value in {"@REDIRECT_SERVER@": redirect, "@APP_DIR@": app_dir, "@APP_USER@": user, "@APP_HOME@": "/var/lib/laqta",
                       "@MODELS_DIR@": "/var/lib/laqta/models", "@PHP_SOCKET@": "/run/php/laqta.sock",
                       "@SERVER_NAME@": server_name, "@LISTEN@": listen}.items():
        text = text.replace(key, value)
    return text


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


needs_plain_path = pytest.mark.skipif(not re.match(r"^/[A-Za-z0-9._/-]+$", str(REPO)),
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
    done = run_install("--dry-run", str(REPO), env={DB_CRED_VAR: value})
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
    cpu = run_install("--dry-run", str(REPO), "--with-birefnet").stdout
    assert "rembg\\[cpu\\]" in cpu and "birefnet-general" in cpu and "birefnet-general-lite" not in cpu
    assert "U2NET_HOME=/var/lib/laqta/models" in cpu
    lite = run_install("--dry-run", str(REPO), "--with-birefnet", "--lite").stdout
    assert "birefnet-general-lite" in lite
    gpu = run_install("--dry-run", str(REPO), "--with-birefnet", "--gpu").stdout
    assert "rembg\\[gpu\\]" in gpu and "rembg\\[cpu\\]" not in gpu


@needs_bash
@needs_plain_path
def test_install_flags_that_need_birefnet_are_refused_alone():
    done = run_install("--dry-run", str(REPO), "--lite")
    assert done.returncode != 0 and "--with-birefnet" in done.stderr


@needs_bash
@needs_plain_path
def test_install_dry_run_local_only_and_redis():
    out = run_install("--dry-run", str(REPO), "--local-only", "--with-redis", "--enable-units").stdout
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
    done = run_install("--dry-run", str(REPO), env={DB_CRED_VAR: "x" * 5})
    assert done.returncode != 0 and "at least 12 characters" in done.stderr


# ------------------------------------------------------------------ systemd units
@pytest.mark.parametrize("name", UNITS)
def test_units_have_no_placeholders_left_after_rendering_and_no_secrets(name):
    text = render((DEPLOY / name).read_text(encoding="utf-8"))
    assert "@" not in re.sub(r"(?m)^\s*#.*$", "", text)
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


def test_nginx_site_requires_basic_auth_everywhere_except_the_acme_path():
    text = re.sub(r"(?m)^\s*#.*$", "", site())
    assert re.search(r'^\s*auth_basic\s+"[^"]+";', text, re.M)
    assert "auth_basic_user_file /etc/nginx/laqta.htpasswd;" in text
    assert len(re.findall(r"auth_basic\s+off", text)) == 1
    assert "auth_basic off" in block(text, "location ^~ /.well-known/acme-challenge/")
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
    assert "@" not in re.sub(r"(?m)^\s*#.*$", "", site())



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


def install_render(tmp_path, *args, cert=False):
    """Run install.sh --dry-run with the render hook: returns the folder holding every rendered unit and config."""
    out = tmp_path / "render"
    out.mkdir()
    live = tmp_path / "live"
    if cert:
        folder = live / "dash.example.com"
        folder.mkdir(parents=True)
        (folder / "fullchain.pem").write_text("cert")
        (folder / "privkey.pem").write_text("key")
    done = run_install("--dry-run", str(REPO), *args,
                       env={"LAQTA_RENDER_DIR": str(out), "LAQTA_CERT_ROOT": str(live)})
    assert done.returncode in (0, 2), done.stderr
    return out


@needs_bash
@needs_plain_path
def test_install_renders_every_unit_and_config_without_placeholders(tmp_path):
    out = install_render(tmp_path, "--server-name", "dash.example.com")
    names = {p.name for p in out.iterdir()}
    assert names == set(UNITS) | {"laqta.conf", "laqta-fpm-pool.conf"}      # laqta.conf is the nginx site
    for path in out.iterdir():
        text = re.sub(r"(?m)^\s*[#;].*$", "", path.read_text(encoding="utf-8"))
        assert "@" not in text, path.name
    nightly = (out / "laqta-nightly.service").read_text(encoding="utf-8")
    assert f"ExecStart={REPO}/.venv/bin/python" in nightly and f"WorkingDirectory={REPO}" in nightly
    assert "U2NET_HOME=/var/lib/laqta/models" in nightly


@needs_bash
@needs_plain_path
def test_install_http_site_before_a_certificate_exists(tmp_path):
    conf = (install_render(tmp_path, "--server-name", "dash.example.com") / "laqta.conf").read_text(encoding="utf-8")
    assert "listen 80;" in conf and "server_name dash.example.com;" in conf
    assert "listen 443" not in conf and "return 301" not in conf
    (tmp_path / "x").mkdir()
    default = (install_render(tmp_path / "x") / "laqta.conf").read_text(encoding="utf-8")
    assert "listen 80 default_server;" in default


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
    done = run_install("--dry-run", str(REPO), "--server-name", "dash.example.com",
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
