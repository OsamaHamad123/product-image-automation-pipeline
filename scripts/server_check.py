"""Server health check: read-only, Arabic output (✅ / ⚠️ / ❌), exit code 1 when anything failed.

Run it on the server after install.sh, after every update, and whenever something looks wrong:

    sudo -u laqta /opt/laqta/.venv/bin/python /opt/laqta/scripts/server_check.py

It changes nothing: no file is written, no table is created, the sheet is opened read-only
(scripts/smoke_live.open_sheet_read_only). It never prints a secret: .env keys are reported by NAME only, and the
only text copied from an error is passed through redact().

Options:
    --only NAME      run only this part (repeatable): python env db sheet cloudinary bg disk units web
    --skip-sheet     do not call Google Sheets (no network)
    --no-systemd     skip the systemd and nginx parts (a laptop, a container)
    --min-free-gb N  fail below N GB of free disk (default 2; below 5 is a warning)

Exit code: 0 = no ❌ (⚠️ are advice), 1 = at least one ❌.
For the paid-provider probes (Serper, Gemini, PhotoRoom ...) use: python scripts/smoke_live.py --probe
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections import namedtuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OK, FAIL, WARN, INFO = "ok", "fail", "warn", "info"
ICONS = {OK: "✅", FAIL: "❌", WARN: "⚠️", INFO: "ℹ️"}
Result = namedtuple("Result", "status text")

MIN_PYTHON = (3, 10)
SECTIONS = ("python", "env", "db", "sheet", "cloudinary", "bg", "disk", "units", "web")
SECTION_TITLES = {
    "python": "بايثون والـ venv",
    "env": "ملفات الإعدادات (.env)",
    "db": "قاعدة البيانات",
    "sheet": "Google Sheets",
    "cloudinary": "Cloudinary",
    "bg": "إزالة الخلفية",
    "disk": "مساحة القرص",
    "units": "الخدمات (systemd)",
    "web": "الموقع (nginx)",
}

# Keys the app cannot work without (names only are ever printed).
REQUIRED_ENV = ("DB_HOST", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD", "SPREADSHEET_NAME_OR_URL",
                "SERPER_API_KEY", "GEMINI_API_KEY",
                "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
# .env key -> the system_settings key the dashboard Settings page saves it under (it overrides .env: config.py).
SETTINGS_KEY = {
    "SERPER_API_KEY": "serper_api_key", "GEMINI_API_KEY": "gemini_api_key",
    "CLOUDINARY_CLOUD_NAME": "cloudinary_cloud_name", "CLOUDINARY_API_KEY": "cloudinary_api_key",
    "CLOUDINARY_API_SECRET": "cloudinary_api_secret", "PHOTOROOM_API_KEY": "photoroom_api_key",
    "BG_REMOVAL_METHOD": "bg_removal_method",
}
SECRET_KEYS = ("SERPER_API_KEY", "GEMINI_API_KEY", "GOOGLE_SEARCH_API_KEY", "CLOUDINARY_API_KEY",
               "CLOUDINARY_API_SECRET", "PHOTOROOM_API_KEY", "REMOVE_BG_API_KEY", "TELEGRAM_BOT_TOKEN",
               "ANTHROPIC_API_KEY", "SERPAPI_API_KEY", "DB_PASSWORD", "APP_KEY")
# Keys also read from the process environment (a real variable beats the .env line).
WATCHED_ENV = REQUIRED_ENV + SECRET_KEYS + ("DB_PORT", "CREDENTIALS_FILE", "U2NET_HOME", "BG_REMOVAL_METHOD",
                                            "REMBG_MODEL", "BIREFNET_MODEL")
DASHBOARD_DB_KEYS = ("DB_HOST", "DB_PORT", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD")

# Names image_processor / rembg can use; any *.onnx in the models folder counts as a model file.
REMBG_MODEL_VARS = ("REMBG_MODEL", "BIREFNET_MODEL")
BG_PHOTOROOM = "photoroom"
BG_REMOVEBG = ("remove_bg_api", "removebg")
BG_LOCAL_FREE = ("grabcut", "none")

SERVICES = (  # (unit, what it is)
    ("nginx.service", "nginx"),
    ("mariadb.service", "MariaDB"),
)
TIMERS = (
    ("laqta-nightly.timer", "التشغيل الليلي"),
    ("laqta-backup.timer", "النسخ الاحتياطي"),
)
NGINX_DIR = "/etc/nginx"


# ------------------------------------------------------------------ helpers
def read_env_file(path):
    """{KEY: value} like config._load_env (blank and # lines skipped, quotes stripped), or None if no file."""
    if not os.path.isfile(path):
        return None
    values = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def example_keys(path):
    """Key names listed in .env.example (active lines only)."""
    keys = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
                if m:
                    keys.append(m.group(1))
    return keys


def is_unset(value):
    """Empty or an .env.example placeholder (your-..., change-me ...)."""
    text = str(value or "").strip().lower()
    return not text or text.startswith(("your-", "your_", "<", "changeme", "change-me")) or text in ("null", "none")


def redact(text, secrets):
    text = str(text)
    for secret in sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)((?:api_?)?key=)[^&\s'\"]+", r"\1[REDACTED]", text)


def secret_values(env):
    return [env.get(k, "") for k in SECRET_KEYS]


def merged(file_values, environ):
    """The real environment wins over the file, as in config._load_env (os.environ.setdefault)."""
    out = dict(file_values or {})
    for key in set(out) | set(WATCHED_ENV):
        if key in environ:
            out[key] = environ[key]
    return out


def effective(key, env, settings):
    """The value the app really uses: the Settings page value (system_settings) first, else .env."""
    from_db = (settings or {}).get(SETTINGS_KEY.get(key, ""), "")
    if not is_unset(from_db):
        return str(from_db)
    value = env.get(key, "")
    return "" if is_unset(value) else value


def _loose_permissions(path):
    if os.name == "nt":
        return False
    try:
        return bool(os.stat(path).st_mode & 0o077)
    except OSError:
        return False


# ------------------------------------------------------------------ checks (each returns a list of Result)
def check_python(version_info=None, prefix=None, root=REPO_ROOT):
    version_info = version_info or sys.version_info
    prefix = prefix or sys.prefix
    out = []
    version = "%d.%d.%d" % tuple(version_info[:3])
    if tuple(version_info[:2]) >= MIN_PYTHON:
        out.append(Result(OK, "بايثون %s (المطلوب %d.%d أو أحدث)" % ((version,) + MIN_PYTHON)))
    else:
        out.append(Result(FAIL, "بايثون %s قديم: لازم %d.%d أو أحدث" % ((version,) + MIN_PYTHON)))
    venv = os.path.join(root, ".venv")
    venv_python = [os.path.join(venv, "bin", "python"), os.path.join(venv, "Scripts", "python.exe")]
    if not any(os.path.exists(p) for p in venv_python):
        out.append(Result(FAIL, "ما في بيئة .venv بمجلد المشروع: شغّل deploy/ubuntu/install.sh"))
    elif os.path.realpath(prefix) == os.path.realpath(venv):
        out.append(Result(OK, "السكربت شغّال من بايثون الـ .venv (نفس اللي بتستعمله اللوحة والخدمات)"))
    else:
        out.append(Result(WARN, "الـ .venv موجودة بس هالسكربت شغّال من بايثون ثاني: استعمل .venv/bin/python"))
    return out


def check_env(root=REPO_ROOT, environ=None, settings=None):
    """Root .env and dashboard/.env: key NAMES only, never a value."""
    environ = os.environ if environ is None else environ
    out = []
    root_file = read_env_file(os.path.join(root, ".env"))
    have_file = root_file is not None
    root_file = root_file or {}
    env = merged(root_file, environ)

    if not have_file:
        out.append(Result(FAIL, "ملف .env ما لقيته بمجلد المشروع: انسخه بـ scp (ما بينعمل لحاله)"))
    else:
        missing = [k for k in REQUIRED_ENV if not effective(k, env, settings)]
        if missing:
            out.append(Result(FAIL, "مفاتيح مطلوبة ناقصة أو فاضية: " + ", ".join(missing)))
        else:
            out.append(Result(OK, "كل المفاتيح المطلوبة موجودة بـ .env (%d مفتاح)" % len(REQUIRED_ENV)))
    if env.get("DB_USERNAME") == "root":
        out.append(Result(WARN, "DB_USERNAME هو root: الأفضل مستخدم مخصص (install.sh بينشئ واحد)"))

    absent = [k for k in example_keys(os.path.join(root, ".env.example")) if k not in root_file]
    if have_file and absent:
        out.append(Result(WARN, "مفاتيح من .env.example مو موجودة بـ .env (اختيارية غالباً): " + ", ".join(absent)))

    dash_file = read_env_file(os.path.join(root, "dashboard", ".env"))
    if dash_file is None:
        out.append(Result(FAIL, "ملف dashboard/.env ناقص: انسخه بـ scp ثم شغّل install.sh مرة ثانية"))
    else:
        dash = merged(dash_file, {})
        if not dash.get("APP_KEY"):
            out.append(Result(FAIL, "APP_KEY فاضي بـ dashboard/.env: شغّل install.sh (بيولّده)"))
        else:
            out.append(Result(OK, "APP_KEY موجود بـ dashboard/.env"))
        if str(dash.get("APP_DEBUG", "")).strip().lower() in ("true", "1", "yes", "on"):
            out.append(Result(FAIL, "APP_DEBUG مفعّل بـ dashboard/.env: لازم false على السيرفر "
                                    "(صفحة الخطأ بتعرض الإعدادات والمفاتيح)"))
        if str(dash.get("APP_ENV", "")).strip().lower() != "production":
            out.append(Result(WARN, "APP_ENV بـ dashboard/.env مو production"))
        differing = [k for k in DASHBOARD_DB_KEYS if root_file and dash.get(k, "") != root_file.get(k, "")]
        if differing:
            out.append(Result(WARN, "إعدادات قاعدة البيانات بـ dashboard/.env مختلفة عن .env الرئيسي: " + ", ".join(differing)))
        cached = os.path.join(root, "dashboard", "bootstrap", "cache", "config.php")
        try:
            if os.path.isfile(cached) and os.path.getmtime(cached) < os.path.getmtime(os.path.join(root, "dashboard", ".env")):
                out.append(Result(WARN, "عدّلت dashboard/.env بعد config:cache: شغّل artisan config:cache (أو install.sh)"))
        except OSError:
            pass

    creds = env.get("CREDENTIALS_FILE") or os.path.join(root, "credentials.json")
    secret_files = [(".env", os.path.join(root, ".env")), ("dashboard/.env", os.path.join(root, "dashboard", ".env")),
                    ("credentials.json", creds)]
    loose = [name for name, path in secret_files if os.path.isfile(path) and _loose_permissions(path)]
    if loose:
        out.append(Result(WARN, "صلاحيات مفتوحة لغير المالك: " + ", ".join(loose) + " (نفّذ chmod 600)"))

    out.extend(check_credentials(creds))
    return out


def check_credentials(path):
    if not os.path.isfile(path):
        return [Result(FAIL, "ملف credentials.json ما لقيته: انسخه بـ scp")]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return [Result(FAIL, "credentials.json موجود بس مو JSON صالح")]
    if not isinstance(doc, dict) or doc.get("type") != "service_account" or not doc.get("client_email") \
            or not doc.get("private_key"):
        return [Result(FAIL, "credentials.json مو مفتاح service account كامل (type, client_email, private_key)")]
    return [Result(OK, "credentials.json مفتاح service account صالح الشكل")]


def expected_tables(root=REPO_ROOT):
    """Tables local_cache_db.init_db() creates, read from the source so this list never drifts."""
    path = os.path.join(root, "local_cache_db.py")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", handle.read())))
    except OSError:
        return []


def _default_connect(**kwargs):
    import pymysql
    return pymysql.connect(**kwargs)


def check_db(env, root=REPO_ROOT, connect=None):
    """(results, settings): can we connect, are the tables there. settings = system_settings rows (kept in memory)."""
    connect = connect or _default_connect
    settings = {}
    try:
        conn = connect(host=env.get("DB_HOST") or "127.0.0.1", port=int(env.get("DB_PORT") or 3306),
                       user=env.get("DB_USERNAME") or "root", password=env.get("DB_PASSWORD", ""),
                       database=env.get("DB_DATABASE") or "automation_db", connect_timeout=5, charset="utf8mb4")
    except Exception as exc:  # noqa: BLE001 - any driver error is a failed check, shown by type and code only
        code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else ""
        return [Result(FAIL, "ما قدرت أتصل بقاعدة البيانات (%s %s): تأكد من DB_* بـ .env وإن mariadb شغّالة"
                       % (type(exc).__name__, code))], settings
    try:
        cur = conn.cursor()
        cur.execute("SHOW TABLES")
        present = {str(row[0]) for row in cur.fetchall()}
        wanted = expected_tables(root)
        missing = [t for t in wanted if t not in present]
        if not wanted:
            results = [Result(WARN, "اتصلت بالقاعدة بس ما قدرت أقرأ قائمة الجداول المتوقعة من local_cache_db.py")]
        elif missing:
            results = [Result(FAIL, "القاعدة موصولة بس ناقصها جداول: %s (شغّل install.sh: بيعمل الـ schema init)"
                              % ", ".join(missing))]
        else:
            results = [Result(OK, "القاعدة موصولة وكل الجداول موجودة (%d جدول)" % len(wanted))]
        if "system_settings" in present:
            try:
                cur.execute("SELECT `key`, `value` FROM system_settings")
                settings = {str(k): ("" if v is None else str(v)) for k, v in cur.fetchall()}
            except Exception:  # noqa: BLE001
                settings = {}
        return results, settings
    except Exception as exc:  # noqa: BLE001
        return [Result(FAIL, "اتصلت بالقاعدة بس فشل الاستعلام (%s)" % type(exc).__name__)], settings
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _default_open_sheet(root=REPO_ROOT):
    path = os.path.join(root, "scripts", "smoke_live.py")
    spec = importlib.util.spec_from_file_location("smoke_live_for_server_check", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, root)
    spec.loader.exec_module(module)
    return module.open_sheet_read_only()


def check_sheet(env, open_sheet=None, skip=False):
    if skip:
        return [Result(INFO, "تخطيت فحص Google Sheets (--skip-sheet)")]
    open_sheet = open_sheet or _default_open_sheet
    sink = io.StringIO()  # config.py prints at import; keep the report clean
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            open_sheet()
    except SystemExit as exc:
        return [Result(FAIL, "ما في اتصال بـ Google: تأكد من credentials.json و CREDENTIALS_FILE (%s)"
                       % redact(exc, secret_values(env))[:120])]
    except Exception as exc:  # noqa: BLE001
        text = redact("%s: %s" % (type(exc).__name__, exc), secret_values(env))[:200]
        return [Result(FAIL, "ما قدرت أفتح الشيت (%s). تأكد إن الشيت مشارك مع client_email الموجود بـ "
                             "credentials.json وإن SPREADSHEET_NAME_OR_URL صح" % text)]
    return [Result(OK, "الاعتمادات فتحت الشيت وورقة المنتجات (قراءة فقط)")]


def check_cloudinary(env, settings=None):
    names = ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
    missing = [k for k in names if not effective(k, env, settings)]
    if missing:
        return [Result(FAIL, "Cloudinary مو مضبوط، ناقص: " + ", ".join(missing))]
    return [Result(OK, "Cloudinary مضبوط (cloud name و key و secret موجودين؛ ما جرّبت اتصال)")]


def models_dir(env, environ=None, home=None):
    return env.get("U2NET_HOME") or environ.get("U2NET_HOME") or os.path.join(home or os.path.expanduser("~"), ".u2net")


def check_bg_removal(env, settings=None, find_spec=None, environ=None, listdir=None):
    find_spec = find_spec or importlib.util.find_spec
    listdir = listdir or os.listdir
    environ = os.environ if environ is None else environ
    method = str(effective("BG_REMOVAL_METHOD", env, settings) or BG_PHOTOROOM).strip().lower()
    out = [Result(INFO, "طريقة إزالة الخلفية المضبوطة: %s" % method)]
    installed = bool(find_spec("rembg")) and bool(find_spec("onnxruntime"))
    folder = models_dir(env, environ)
    try:
        files = sorted(f for f in listdir(folder) if f.endswith(".onnx"))
    except OSError:
        files = []
    if method == BG_PHOTOROOM:
        out.append(Result(OK, "PhotoRoom: المفتاح موجود") if effective("PHOTOROOM_API_KEY", env, settings)
                   else Result(FAIL, "الطريقة photoroom بس PHOTOROOM_API_KEY ناقص"))
    elif method in BG_REMOVEBG:
        out.append(Result(OK, "remove.bg: المفتاح موجود") if effective("REMOVE_BG_API_KEY", env, settings)
                   else Result(FAIL, "الطريقة remove.bg بس REMOVE_BG_API_KEY ناقص"))
    elif method in BG_LOCAL_FREE:
        out.append(Result(OK, "الطريقة %s محلية وما بتحتاج مفتاح" % method))
    elif method == "rembg":
        if not installed:
            out.append(Result(FAIL, "الطريقة rembg بس rembg/onnxruntime مو منصّبين: install.sh --with-birefnet"))
        else:
            wanted = next((env.get(v) or environ.get(v) for v in REMBG_MODEL_VARS if env.get(v) or environ.get(v)), "")
            if wanted:
                out.append(Result(OK, "موديل %s موجود" % wanted) if (wanted + ".onnx") in files
                           else Result(FAIL, "موديل %s.onnx مو موجود بـ %s: install.sh --with-birefnet" % (wanted, folder)))
            elif files:
                out.append(Result(OK, "ملفات الموديل بـ %s: %s" % (folder, ", ".join(files))))
            else:
                out.append(Result(FAIL, "ما في ملف موديل (.onnx) بـ %s: install.sh --with-birefnet" % folder))
    else:
        out.append(Result(WARN, "قيمة BG_REMOVAL_METHOD غير معروفة: %s" % method))
    if method != "rembg":
        if installed and files:
            out.append(Result(INFO, "rembg منصّب وفيه موديلات (%s) بس مو مستعمل حالياً" % ", ".join(files)))
        elif installed:
            out.append(Result(INFO, "rembg منصّب بس ما في موديل محمّل"))
        else:
            out.append(Result(INFO, "rembg/BiRefNet مو منصّب (اختياري: install.sh --with-birefnet)"))
    return out


def check_disk(path, min_free_gb=2.0, warn_free_gb=5.0, disk_usage=None):
    disk_usage = disk_usage or shutil.disk_usage
    try:
        free_gb = disk_usage(path).free / (1024 ** 3)
    except OSError as exc:
        return [Result(FAIL, "ما قدرت أقرأ مساحة القرص (%s)" % type(exc).__name__)]
    text = "المساحة الفاضية %.1f GB بـ %s" % (free_gb, path)
    if free_gb < min_free_gb:
        return [Result(FAIL, text + " (أقل من %.0f GB: نظّف القرص)" % min_free_gb)]
    if free_gb < warn_free_gb:
        return [Result(WARN, text + " (قليلة: الموديلات والصور والنسخ الاحتياطية بتاكل مساحة)")]
    return [Result(OK, text)]


def _systemctl(run, *args):
    try:
        done = run(["systemctl", *args], capture_output=True, text=True, timeout=15)
        return (done.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def systemd_available(which=None, exists=None):
    which = which or shutil.which
    exists = exists or os.path.isdir
    return bool(which("systemctl")) and exists("/run/systemd/system")


def check_units(run=None):
    run = run or subprocess.run
    out = []
    for unit, label in TIMERS:
        state = _systemctl(run, "is-active", unit)
        if state == "active":
            nxt = _systemctl(run, "show", unit, "-p", "NextElapseUSecRealtime", "--value")
            out.append(Result(OK, "%s شغّال (%s)%s" % (label, unit, ("، الجولة الجاية: " + nxt) if nxt else "")))
        else:
            out.append(Result(FAIL, "%s مو شغّال (%s: %s): systemctl enable --now %s"
                              % (label, unit, state or "غير معروف", unit)))
    worker = "laqta-sync-worker.service"
    if _systemctl(run, "is-enabled", worker) == "enabled":
        state = _systemctl(run, "is-active", worker)
        out.append(Result(OK, "عامل مزامنة الشيت شغّال (%s)" % worker) if state == "active"
                   else Result(FAIL, "عامل مزامنة الشيت مفعّل بس مو شغّال (%s): journalctl -u %s" % (state or "?", worker)))
    else:
        out.append(Result(INFO, "عامل مزامنة الشيت (Redis) مو مفعّل: طبيعي إذا ما بتستعمل Redis"))
    listing = _systemctl(run, "list-unit-files", "php*-fpm.service", "--no-legend")
    fpm = next((line.split()[0] for line in listing.splitlines() if line.split()), "")
    services = list(SERVICES) + ([(fpm, "PHP-FPM")] if fpm else [])
    if not fpm:
        out.append(Result(FAIL, "ما لقيت خدمة php-fpm: install.sh بينصّبها"))
    for unit, label in services:
        state = _systemctl(run, "is-active", unit)
        out.append(Result(OK, "%s شغّال (%s)" % (label, unit)) if state == "active"
                   else Result(FAIL, "%s مو شغّال (%s: %s)" % (label, unit, state or "غير معروف")))
    return out


def check_web(nginx_dir=NGINX_DIR):
    """The dashboard has no login of its own: the nginx site must have basic auth and a password file."""
    site = os.path.join(nginx_dir, "sites-enabled", "laqta.conf")
    if not os.path.exists(site):
        return [Result(FAIL, "موقع nginx (laqta.conf) مو مفعّل: شغّل install.sh")]
    try:
        with open(site, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return [Result(WARN, "ما قدرت أقرأ %s (جرّب بـ sudo)" % site)]
    out = []
    htpasswd = os.path.join(nginx_dir, "laqta.htpasswd")
    has_auth = re.search(r"^\s*auth_basic\s+(?!off)", text, re.M) is not None
    try:
        has_users = os.path.getsize(htpasswd) > 0
    except OSError:
        has_users = False
    if has_auth and has_users:
        out.append(Result(OK, "اللوحة محمية بكلمة سر (auth_basic + ملف المستخدمين)"))
    else:
        out.append(Result(FAIL, "اللوحة بلا كلمة سر! أي حدا بيوصل إلها بيقدر يعتمد وينشر: شغّل install.sh --reset-auth"))
    local_only = re.search(r"listen\s+127\.0\.0\.1:", text) is not None
    if local_only:
        out.append(Result(OK, "الموقع على localhost بس (نفق SSH أو Tailscale)"))
    elif re.search(r"ssl_certificate\b|listen\s+[^;]*\b443\b", text):
        out.append(Result(OK, "الموقع على HTTPS"))
    else:
        out.append(Result(WARN, "الموقع على HTTP بدون تشفير: كلمة السر بتنبعت مكشوفة. شغّل certbot (docs/deploy_ubuntu.md)"))
    return out


# ------------------------------------------------------------------ runner
def run_checks(selected, root, environ, args, deps=None):
    """[(section, [Result])] in a fixed order. deps = injectable pieces for tests."""
    deps = deps or {}
    file_env = read_env_file(os.path.join(root, ".env"))
    env = merged(file_env, environ)
    results = {}
    settings = {}

    if "python" in selected:
        results["python"] = check_python(root=root, **deps.get("python", {}))
    if selected & {"db", "env", "cloudinary", "bg"}:  # system_settings can hold keys that .env lacks
        db_results, settings = check_db(env, root=root, connect=deps.get("connect"))
        if "db" in selected:
            results["db"] = db_results
    if "env" in selected:
        results["env"] = check_env(root=root, environ=environ, settings=settings)
    if "sheet" in selected:
        results["sheet"] = check_sheet(env, open_sheet=deps.get("open_sheet"), skip=args.skip_sheet)
    if "cloudinary" in selected:
        results["cloudinary"] = check_cloudinary(env, settings)
    if "bg" in selected:
        results["bg"] = check_bg_removal(env, settings, find_spec=deps.get("find_spec"), environ=environ,
                                         listdir=deps.get("listdir"))
    if "disk" in selected:
        results["disk"] = check_disk(root, min_free_gb=args.min_free_gb, disk_usage=deps.get("disk_usage"))
    on_server = (not args.no_systemd) and systemd_available(deps.get("which"), deps.get("isdir"))
    for name, fn in (("units", lambda: check_units(run=deps.get("run"))),
                     ("web", lambda: check_web(nginx_dir=deps.get("nginx_dir", NGINX_DIR)))):
        if name in selected:
            results[name] = fn() if on_server else [Result(INFO, "تخطيت هالفحص: هاد الجهاز مو سيرفر systemd")]
    return [(name, results[name]) for name in SECTIONS if name in results]


def render(sections):
    lines = []
    counts = {OK: 0, WARN: 0, FAIL: 0, INFO: 0}
    for name, items in sections:
        lines.append("")
        lines.append("== %s ==" % SECTION_TITLES[name])
        for item in items:
            counts[item.status] += 1
            lines.append("%s %s" % (ICONS[item.status], item.text))
    lines.append("")
    lines.append("الخلاصة: %d ✅  %d ⚠️  %d ❌" % (counts[OK], counts[WARN], counts[FAIL]))
    lines.append("كل شي تمام" if not counts[FAIL] else "في %d مشكلة لازم تنحل قبل ما تعتمد على السيرفر" % counts[FAIL])
    return "\n".join(lines), counts[FAIL]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="فحص صحة السيرفر (قراءة فقط)")
    parser.add_argument("--only", action="append", choices=SECTIONS, help="شغّل هالجزء بس (ممكن تكراره)")
    parser.add_argument("--skip-sheet", action="store_true", help="لا تتصل بـ Google Sheets")
    parser.add_argument("--no-systemd", action="store_true", help="تخطّى فحص systemd و nginx")
    parser.add_argument("--min-free-gb", type=float, default=2.0, help="أقل مساحة فاضية (GB) قبل ما يفشل الفحص")
    parser.add_argument("--root", default=REPO_ROOT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None, deps=None, environ=None, out=None):
    args = parse_args(argv)
    environ = os.environ if environ is None else environ
    selected = set(args.only or SECTIONS)
    sections = run_checks(selected, args.root, environ, args, deps)
    text, failures = render(sections)
    stream = out or sys.stdout
    with contextlib.suppress(Exception):
        stream.reconfigure(encoding="utf-8")
    print(text, file=stream)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
