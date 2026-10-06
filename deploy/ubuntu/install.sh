#!/usr/bin/env bash
# install.sh - set up (or update) the Laqta product-image pipeline on Ubuntu 22.04 / 24.04.
# Safe to re-run: every step checks before it changes anything. Full guide (Arabic): docs/deploy_ubuntu.md
#
#   sudo LAQTA_DB_PASSWORD='<long password>' bash deploy/ubuntu/install.sh /opt/laqta [options]
#
# Run it as root from a git clone of the repository (the app dir is that clone). It never creates or edits .env
# or credentials.json: copy them yourself (scp). The only change to a .env is `php artisan key:generate`, which
# writes APP_KEY into dashboard/.env when that key is empty.
set -euo pipefail
umask 022
trap 'echo "install.sh: failed at line $LINENO (command: $BASH_COMMAND)" >&2' ERR

usage() {
    cat <<'EOF'
Usage: sudo [LAQTA_DB_PASSWORD=...] bash deploy/ubuntu/install.sh [APP_DIR] [options]

  APP_DIR              the git clone to install from (default: the clone this script lives in)

Options
  --user NAME          system user that runs everything (default: laqta, created if missing)
  --server-name HOST   public host name for nginx (default: any host); use it with certbot
  --local-only         nginx listens on 127.0.0.1:8080 only (SSH tunnel / Tailscale, no public port)
  --with-birefnet      pip install rembg[cpu] and download the birefnet-general model
      --lite           ...the smaller birefnet-general-lite model instead
      --gpu            ...rembg[gpu] (needs an NVIDIA GPU with CUDA libraries)
  --with-redis         also install redis-server (only if you use Redis); --enable-units then starts the sync worker
  --enable-units       enable and start the nightly + backup timers (and the sync worker with --with-redis)
  --reset-auth         type a new password for the dashboard login (nginx basic auth)
  --auth-user NAME     login name for the dashboard (default: admin)
  --php-version X.Y    PHP version to install (default: 8.3; on 22.04 it comes from ppa:ondrej/php)
  --skip-apt           do not run apt (re-runs when the packages are already installed)
  --dry-run            print what would be done, change nothing
  -h, --help           this text

Environment (read at run time, never stored in the repo)
  LAQTA_DB_NAME        database name       (default: DB_DATABASE from .env, else automation_db)
  LAQTA_DB_USER        database user       (default: laqta_app)
  LAQTA_DB_PASSWORD    its password, 12+ characters. Set it on the first install to create the database and
                       user; without it the database step only runs the schema upgrade with the .env credentials.
EOF
}

# ---------------------------------------------------------------- defaults and arguments
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_DIR=""
APP_USER="laqta"
SERVER_NAME="_"
LOCAL_ONLY=0
WITH_BIREFNET=0
LITE=0
GPU=0
WITH_REDIS=0
ENABLE_UNITS=0
RESET_AUTH=0
AUTH_USER="admin"
PHP_VERSION="8.3"
SKIP_APT=0
DRY_RUN=0
PROBLEMS=()

while (($#)); do
    case "$1" in
        --user) APP_USER="${2:?--user needs a value}"; shift ;;
        --server-name) SERVER_NAME="${2:?--server-name needs a value}"; shift ;;
        --local-only) LOCAL_ONLY=1 ;;
        --with-birefnet) WITH_BIREFNET=1 ;;
        --lite) LITE=1 ;;
        --gpu) GPU=1 ;;
        --with-redis) WITH_REDIS=1 ;;
        --enable-units) ENABLE_UNITS=1 ;;
        --reset-auth) RESET_AUTH=1 ;;
        --auth-user) AUTH_USER="${2:?--auth-user needs a value}"; shift ;;
        --php-version) PHP_VERSION="${2:?--php-version needs a value}"; shift ;;
        --skip-apt) SKIP_APT=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "unknown option: $1" >&2; usage >&2; exit 64 ;;
        *) if [[ -z "$APP_DIR" ]]; then APP_DIR="$1"; else echo "only one APP_DIR is allowed" >&2; exit 64; fi ;;
    esac
    shift
done

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
problem() { PROBLEMS+=("$*"); warn "$*"; }

# run CMD...: execute, or only print under --dry-run (never put a secret on a command line).
run() {
    if ((DRY_RUN)); then
        printf '+'; printf ' %q' "$@"; printf '\n'
        return 0
    fi
    "$@"
}

# ---------------------------------------------------------------- checks
APP_DIR="${APP_DIR:-$REPO_ROOT}"
[[ "$APP_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "APP_DIR must be an absolute path made of letters, digits and . _ / - (got: $APP_DIR)"
[[ -f "$APP_DIR/requirements.txt" && -f "$APP_DIR/dashboard/artisan" ]] \
    || die "$APP_DIR is not a clone of the repository (requirements.txt / dashboard/artisan missing): git clone it there first"
APP_DIR="$(cd "$APP_DIR" && pwd)"
[[ "$APP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid --user: $APP_USER"
[[ "$SERVER_NAME" =~ ^([A-Za-z0-9._-]+|_)$ ]] || die "invalid --server-name: $SERVER_NAME"
[[ "$AUTH_USER" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid --auth-user: $AUTH_USER"
[[ "$PHP_VERSION" =~ ^8\.[0-9]+$ ]] || die "invalid --php-version: $PHP_VERSION (the dashboard needs PHP 8.2 or newer)"
if ((LITE || GPU)) && ((!WITH_BIREFNET)); then die "--lite and --gpu only make sense together with --with-birefnet"; fi
if ((!DRY_RUN)) && [[ $EUID -ne 0 ]]; then die "run as root: sudo bash $0 ..."; fi

OS_ID="unknown"; OS_VER=""
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"; OS_VER="${VERSION_ID:-}"
fi
if [[ "$OS_ID" != "ubuntu" || ! "$OS_VER" =~ ^(22\.04|24\.04)$ ]]; then
    warn "tested on Ubuntu 22.04 and 24.04 only (this is: $OS_ID $OS_VER)"
fi

APP_HOME="/var/lib/$APP_USER"
MODELS_DIR="$APP_HOME/models"
VENV="$APP_DIR/.venv"
DASH="$APP_DIR/dashboard"
PHP_BIN="/usr/bin/php$PHP_VERSION"
PHP_SOCKET="/run/php/laqta.sock"
AUTH_FILE="/etc/nginx/laqta.htpasswd"

# .env names are read for the database name only (a name, not a secret).
env_value() { # env_value FILE KEY -> the value (quotes stripped), empty if absent
    [[ -f "$1" ]] || return 0
    sed -n "s/^$2=//p" "$1" | head -n1 | tr -d "\"'\r"
}
DB_NAME="${LAQTA_DB_NAME:-$(env_value "$APP_DIR/.env" DB_DATABASE)}"
DB_NAME="${DB_NAME:-automation_db}"
DB_USER="${LAQTA_DB_USER:-laqta_app}"
[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || die "database name may contain only letters, digits and _"
[[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ ]] || die "database user may contain only letters, digits and _"

as_app() { runuser -u "$APP_USER" -- env "HOME=$APP_HOME" "$@"; }

# render TEMPLATE DEST MODE: fill the @NAME@ words and install the file only when its content changed.
render() {
    local template="$1" dest="$2" mode="$3" text tmp
    text="$(<"$template")"
    text="${text//@APP_DIR@/$APP_DIR}"
    text="${text//@APP_USER@/$APP_USER}"
    text="${text//@APP_HOME@/$APP_HOME}"
    text="${text//@MODELS_DIR@/$MODELS_DIR}"
    text="${text//@PHP_SOCKET@/$PHP_SOCKET}"
    text="${text//@SERVER_NAME@/$SERVER_NAME}"
    text="${text//@LISTEN@/$LISTEN_LINES}"
    text="${text//@REDIRECT_SERVER@/$REDIRECT_SERVER}"
    if ((DRY_RUN)); then
        echo "+ render $(basename "$template") -> $dest (mode $mode)"
        # LAQTA_RENDER_DIR: test hook, keeps the rendered text of every unit and config for inspection
        if [[ -n "${LAQTA_RENDER_DIR:-}" ]]; then printf '%s\n' "$text" > "$LAQTA_RENDER_DIR/$(basename "$template")"; fi
        return 0
    fi
    tmp="$(mktemp)"
    printf '%s\n' "$text" > "$tmp"
    if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
        rm -f "$tmp"; chmod "$mode" "$dest"
    else
        install -m "$mode" "$tmp" "$dest"; rm -f "$tmp"
        echo "    wrote $dest"
    fi
}

# HTTPS: once `certbot certonly --webroot` has issued a certificate for --server-name, this script renders the 443
# server itself (so a re-run keeps HTTPS; certbot never has to edit our file). Before that: plain port 80.
CERT_DIR="${LAQTA_CERT_ROOT:-/etc/letsencrypt/live}/$SERVER_NAME"   # LAQTA_CERT_ROOT: test hook
REDIRECT_SERVER=""
TLS=0
if ((LOCAL_ONLY)); then
    LISTEN_LINES="listen 127.0.0.1:8080;"
elif [[ "$SERVER_NAME" != "_" && -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" ]]; then
    TLS=1
    LISTEN_LINES="listen 443 ssl;
    listen [::]:443 ssl;
    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security \"max-age=31536000\" always;"
    REDIRECT_SERVER="# Port 80 serves nothing but the certbot challenge and a redirect to HTTPS (no dashboard, no login form).
server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_NAME;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}"
elif [[ "$SERVER_NAME" == "_" ]]; then
    LISTEN_LINES=$'listen 80 default_server;\n    listen [::]:80 default_server;'
else
    LISTEN_LINES=$'listen 80;\n    listen [::]:80;'
fi

cd "$APP_DIR"

# ---------------------------------------------------------------- 1. packages
step_packages() {
    log "1/10 system packages"
    if ((SKIP_APT)); then echo "    skipped (--skip-apt)"; return 0; fi
    export DEBIAN_FRONTEND=noninteractive
    run apt-get update -qq
    if [[ "$OS_VER" == "22.04" ]]; then
        # 22.04 ships PHP 8.1; the dashboard (Laravel 11) needs 8.2 or newer.
        run apt-get install -y -qq software-properties-common
        if ! grep -rqs "ondrej/php" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
            run add-apt-repository -y ppa:ondrej/php
            run apt-get update -qq
        fi
    fi
    local v="$PHP_VERSION"
    local -a packages=(ca-certificates curl git unzip
        python3 python3-venv python3-pip libgl1 libglib2.0-0
        mariadb-server mariadb-client nginx apache2-utils
        "php$v-fpm" "php$v-cli" "php$v-mbstring" "php$v-xml" "php$v-curl" "php$v-mysql" "php$v-zip" "php$v-intl"
        composer certbot python3-certbot-nginx)
    if ((WITH_REDIS)); then packages+=(redis-server); fi
    run apt-get install -y -qq "${packages[@]}"
}

# ---------------------------------------------------------------- 2. user and folders
step_user() {
    log "2/10 app user $APP_USER and folders"
    if ! id -u "$APP_USER" >/dev/null 2>&1; then
        run useradd --system --create-home --home-dir "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
    fi
    run install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_HOME" "$MODELS_DIR"
    run install -d -m 755 /var/www/letsencrypt
    run install -d -m 700 /var/backups/laqta
    run chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    run chmod 755 "$APP_DIR"
    local f
    for f in .env credentials.json dashboard/.env; do
        if [[ -f "$APP_DIR/$f" ]]; then run chmod 600 "$APP_DIR/$f"; fi
    done
    run install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$APP_DIR/temp" "$APP_DIR/runs"
}

# ---------------------------------------------------------------- 3. python venv
step_python() {
    log "3/10 Python venv and requirements"
    if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        die "python3 is older than 3.10 (the pipeline needs 3.10 or newer)"
    fi
    if [[ ! -x "$VENV/bin/python" ]]; then
        run as_app python3 -m venv "$VENV"
    fi
    run as_app "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip wheel
    run as_app "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
    # dashboard/app/Services/PythonBridge.php already prefers <repo>/.venv/bin/python, so nothing more to point.
    if ((!DRY_RUN)) && [[ ! -x "$VENV/bin/python" ]]; then die "the venv has no $VENV/bin/python"; fi
}

# ---------------------------------------------------------------- 4. local background removal (optional)
REMBG_VERSION="2.0.85"

step_birefnet() {
    if ((!WITH_BIREFNET)); then return 0; fi
    log "4/10 rembg + BiRefNet model"
    local extra="cpu" model="birefnet-general"
    if ((GPU)); then extra="gpu"; fi
    if ((LITE)); then model="birefnet-general-lite"; fi
    # pinned: rembg 2.0.79+ has remove(decontaminate=True) (image_processor passes it when remove() accepts it); 2.0.85
    # is the release checked. The model is always ours (birefnet-general[-lite]): never rembg's own default bria-rmbg
    # (CC BY-NC weights).
    run as_app "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "rembg[$extra]==$REMBG_VERSION"
    # Download into the shared models folder and prove the model loads (needs RAM: the plain model is large).
    run as_app env "U2NET_HOME=$MODELS_DIR" "$VENV/bin/python" -c \
        'import sys; from rembg import new_session; new_session(sys.argv[1]); print("    model ready:", sys.argv[1])' "$model"
    echo "    set BG_REMOVAL_METHOD=rembg in .env (or the dashboard settings page) to use it"
}

# ---------------------------------------------------------------- 5. dashboard (Laravel)
step_dashboard() {
    log "5/10 dashboard (composer, caches, storage)"
    local d
    for d in storage/app storage/framework/cache/data storage/framework/sessions storage/framework/views storage/logs bootstrap/cache; do
        run install -d -o "$APP_USER" -g "$APP_USER" -m 775 "$DASH/$d"
    done
    run as_app env COMPOSER_NO_INTERACTION=1 "COMPOSER_HOME=$APP_HOME/.composer" \
        "$PHP_BIN" /usr/bin/composer install --no-dev --prefer-dist --optimize-autoloader --no-interaction --working-dir="$DASH"
    if [[ ! -f "$DASH/.env" ]]; then
        problem "dashboard/.env is missing: copy it (scp), then run install.sh again (APP_KEY, config:cache and route:cache wait for it)"
        return 0
    fi
    if ! grep -Eq '^APP_KEY=[^[:space:]]+' "$DASH/.env"; then
        echo "    APP_KEY is empty: generating one"
        run as_app "$PHP_BIN" "$DASH/artisan" config:clear
        run as_app "$PHP_BIN" "$DASH/artisan" key:generate --force
    fi
    run as_app "$PHP_BIN" "$DASH/artisan" config:cache
    run as_app "$PHP_BIN" "$DASH/artisan" route:cache
    run chmod 600 "$DASH/.env"
}

# ---------------------------------------------------------------- 6. database
sql_escape() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\'/\\\'}"; printf '%s' "$s"; }

step_database() {
    log "6/10 MariaDB database and schema"
    run systemctl enable --now mariadb
    local password="${LAQTA_DB_PASSWORD:-}"
    if [[ -n "$password" ]]; then
        [[ ${#password} -ge 12 ]] || die "LAQTA_DB_PASSWORD must be at least 12 characters"
        [[ "$password" != *$'\n'* && "$password" != *$'\r'* ]] || die "LAQTA_DB_PASSWORD must be one line"
        if ((DRY_RUN)); then
            echo "+ create database $DB_NAME and user $DB_USER (password from LAQTA_DB_PASSWORD)"
        else
            local p; p="$(sql_escape "$password")"
            # SQL goes through stdin, so the password never appears in a process list.
            mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$p';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$p';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$p';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$p';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
            echo "    database $DB_NAME and user $DB_USER are ready; put DB_DATABASE, DB_USERNAME and DB_PASSWORD in .env and dashboard/.env"
        fi
    else
        echo "    LAQTA_DB_PASSWORD not set: database and user are not (re)created; the schema upgrade uses the .env credentials"
    fi
    # local_cache_db.init_db(): CREATE TABLE IF NOT EXISTS + the idempotent upgrades. Run it on every install and update.
    local ok=1
    if [[ -n "$password" ]]; then
        (export DB_HOST=127.0.0.1 DB_PORT=3306 "DB_DATABASE=$DB_NAME" "DB_USERNAME=$DB_USER" "DB_PASSWORD=$password"
         run as_app "$VENV/bin/python" -c 'import sys, local_cache_db; sys.exit(0 if local_cache_db.init_db() else 1)') || ok=0
    else
        run as_app "$VENV/bin/python" -c 'import sys, local_cache_db; sys.exit(0 if local_cache_db.init_db() else 1)' || ok=0
    fi
    if ((!ok)); then problem "schema init failed: check DB_HOST / DB_DATABASE / DB_USERNAME / DB_PASSWORD in .env, then run install.sh again"; fi
}

# ---------------------------------------------------------------- 7. systemd units
step_units() {
    log "7/10 systemd units and backup script"
    local u
    for u in laqta-nightly.service laqta-nightly.timer laqta-sync-worker.service laqta-backup.service laqta-backup.timer; do
        render "$SCRIPT_DIR/$u" "/etc/systemd/system/$u" 644
    done
    run install -m 755 -o root -g root "$SCRIPT_DIR/backup.sh" /usr/local/sbin/laqta-backup
    if [[ ! -f /etc/default/laqta-backup ]]; then
        if ((DRY_RUN)); then
            echo "+ write /etc/default/laqta-backup (DB_NAME=$DB_NAME)"
        else
            printf '# Settings for laqta-backup.service (see deploy/ubuntu/backup.sh)\nDB_NAME=%s\n#BACKUP_DIR=/var/backups/laqta\n#KEEP_DAYS=14\n' "$DB_NAME" > /etc/default/laqta-backup
            chmod 644 /etc/default/laqta-backup
        fi
    fi
    run systemctl daemon-reload
    if ((ENABLE_UNITS)); then
        run systemctl enable --now laqta-backup.timer laqta-nightly.timer
        if ((WITH_REDIS)); then run systemctl enable --now redis-server laqta-sync-worker.service; fi
    else
        echo "    timers are installed but NOT enabled (run server_check.py first, then: systemctl enable --now laqta-nightly.timer laqta-backup.timer)"
    fi
    # A running sync worker keeps old code in memory: restart it. The nightly run is never restarted here
    # (a restart would kill a night in progress); the next night simply uses the new code.
    if ((!DRY_RUN)) && systemctl is-active --quiet laqta-sync-worker.service; then
        systemctl restart laqta-sync-worker.service
    fi
}

# ---------------------------------------------------------------- 8. php-fpm pool
step_php_pool() {
    log "8/10 php-fpm pool (runs as $APP_USER)"
    render "$SCRIPT_DIR/laqta-fpm-pool.conf" "/etc/php/$PHP_VERSION/fpm/pool.d/laqta.conf" 644
    run "/usr/sbin/php-fpm$PHP_VERSION" -t
    run systemctl enable "php$PHP_VERSION-fpm"
    # reload, not restart: the dashboard starts the background worker from php-fpm, and a restart would kill it.
    run systemctl reload-or-restart "php$PHP_VERSION-fpm"
}

# ---------------------------------------------------------------- 9. nginx with basic auth
step_nginx() {
    log "9/10 nginx site (basic auth required)"
    render "$SCRIPT_DIR/laqta.conf" /etc/nginx/sites-available/laqta.conf 644
    local have_auth=1
    if ((DRY_RUN)); then
        echo "+ htpasswd -B [-c] $AUTH_FILE $AUTH_USER   (interactive password prompt; kept when the file already exists)"
    elif [[ ! -s "$AUTH_FILE" || "$RESET_AUTH" -eq 1 ]]; then
        if [[ -t 0 ]]; then
            echo "    Type the dashboard password for user '$AUTH_USER' (twice):"
            if [[ -s "$AUTH_FILE" ]]; then htpasswd -B "$AUTH_FILE" "$AUTH_USER"; else htpasswd -B -c "$AUTH_FILE" "$AUTH_USER"; fi
        else
            have_auth=0
            problem "no dashboard password file and no terminal to type one: the nginx site is NOT enabled. Run install.sh from an interactive shell (or: htpasswd -B -c $AUTH_FILE $AUTH_USER)"
        fi
    fi
    if ((!DRY_RUN)) && [[ -f "$AUTH_FILE" ]]; then chown root:www-data "$AUTH_FILE"; chmod 640 "$AUTH_FILE"; fi
    if ((!have_auth)); then return 0; fi
    run ln -sfn /etc/nginx/sites-available/laqta.conf /etc/nginx/sites-enabled/laqta.conf
    if ((!LOCAL_ONLY)) && [[ "$SERVER_NAME" == "_" && -L /etc/nginx/sites-enabled/default ]]; then
        echo "    removing nginx's default welcome site (this site is the default_server now)"
        run rm -f /etc/nginx/sites-enabled/default
    fi
    run nginx -t
    run systemctl enable nginx
    run systemctl reload-or-restart nginx
    if ((LOCAL_ONLY)); then
        echo "    local only: from your PC run  ssh -L 8080:127.0.0.1:8080 USER@SERVER  then open http://127.0.0.1:8080/"
    elif ((TLS)); then
        echo "    HTTPS is on for $SERVER_NAME (certificate in $CERT_DIR)"
    elif [[ "$SERVER_NAME" != "_" ]]; then
        echo "    HTTPS next: certbot certonly --webroot -w /var/www/letsencrypt -d $SERVER_NAME, then run install.sh again (docs/deploy_ubuntu.md)"
    else
        warn "served on plain HTTP: the basic-auth password travels unencrypted. Use --server-name + certbot, or --local-only"
    fi
}

# ---------------------------------------------------------------- 10. what is still missing
step_report() {
    log "10/10 .env keys still missing (names only, values are never printed)"
    run as_app "$VENV/bin/python" "$APP_DIR/scripts/server_check.py" --only env || true
    echo
    if ((${#PROBLEMS[@]})); then
        echo "Finished, but these need attention:"
        printf '  - %s\n' "${PROBLEMS[@]}"
    else
        echo "Finished."
    fi
    echo "Next: fix the keys above in .env (scp it, never commit it), then:"
    echo "  sudo -u $APP_USER $VENV/bin/python $APP_DIR/scripts/server_check.py"
    echo "  sudo systemctl enable --now laqta-nightly.timer laqta-backup.timer      (if not done with --enable-units)"
    echo "After changing dashboard/.env: sudo -u $APP_USER php$PHP_VERSION $DASH/artisan config:cache"
}

step_packages
step_user
step_python
step_birefnet
step_dashboard
step_database
step_units
step_php_pool
step_nginx
step_report
# Exit 2 = finished, but something above needs attention (missing dashboard/.env, schema init failed, no password file).
if ((${#PROBLEMS[@]})); then exit 2; fi
