#!/usr/bin/env bash
# update.sh - update an installed Laqta to a new version of the code, in one command, and roll back by itself
# when the new version does not answer. Only the app: nginx, the php-fpm pool and the systemd units stay as
# install.sh (or the owner) left them, so it is safe on a server shared with other sites.
#
#   sudo bash deploy/ubuntu/update.sh [--bundle FILE] [--ref REF]
#
#   --bundle FILE   take the new commits from a git bundle (made on a machine that has them:
#                   git bundle create u.bundle <server-commit>..origin/main) instead of `git fetch origin`
#   --ref REF       the commit to go to (default: origin/main); fast-forward only
#
# Settings (environment or /etc/default/laqta-update):
#   APP_DIR        the git clone that is served (default: the clone this script lives in)
#   APP_USER       the user that owns and runs the app (default: owner of APP_DIR/dashboard/storage)
#   HEALTH_URL     checked after the update (default: https://SITE_HOST/healthz sent to 127.0.0.1, or
#                  http://127.0.0.1:8080/healthz without a host name, as install.sh --local-only serves it)
#   SITE_HOST      the dashboard's host name, for the health check (default: APP_URL's host from dashboard/.env)
#   PHP_BIN        php for artisan (default: php)
#
# Steps: backup (laqta-backup.service when installed) -> maintenance on -> fast-forward the code -> pip / composer
# only when their lock files changed -> migrate -> Python tables (schema_mark) -> caches -> maintenance off ->
# health check. A failure after the code moved puts the old commit back (the database is not restored: the
# migrations only add, and the old code runs on them; restore a dump by hand if ever needed, docs section 14).
set -euo pipefail
set -o errtrace   # the ERR trap (rollback) also fires inside functions
umask 022

# shellcheck source=/dev/null
[[ -f /etc/default/laqta-update ]] && source /etc/default/laqta-update

BUNDLE=""
REF="origin/main"
while (($#)); do
    case "$1" in
        --bundle) BUNDLE="${2:?--bundle needs a file}"; shift 2 ;;
        --ref) REF="${2:?--ref needs a commit}"; shift 2 ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "update.sh: unknown option $1" >&2; exit 64 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DASH="$APP_DIR/dashboard"
APP_USER="${APP_USER:-$(stat -c '%U' "$DASH/storage")}"
PHP_BIN="${PHP_BIN:-php}"
VENV_PY="$APP_DIR/.venv/bin/python"

log() { printf '\n==> %s\n' "$*"; }
die() { echo "update.sh: $*" >&2; exit 1; }
as_app() { sudo -u "$APP_USER" -H "$@"; }
git_app() { git -C "$APP_DIR" "$@"; }   # as root, like install.sh's clone (safe.directory below)
artisan() { (cd "$DASH" && as_app "$PHP_BIN" artisan "$@"); }

(( EUID == 0 )) || die "run it as root (sudo bash deploy/ubuntu/update.sh)"
[[ -d "$APP_DIR/.git" ]] || die "$APP_DIR is not a git clone"
[[ -x "$VENV_PY" ]] || die "$VENV_PY not found: install.sh makes the virtualenv"
git config --global --get-all safe.directory | grep -qxF "$APP_DIR" || git config --global --add safe.directory "$APP_DIR"
[[ -z "$(git_app status --porcelain --untracked-files=no)" ]] \
    || die "tracked files were changed on the server (git status): commit or discard them first"

if [[ -z "${SITE_HOST:-}" ]]; then
    SITE_HOST="$(sed -n 's#^APP_URL=["'\'']\{0,1\}https\{0,1\}://\([^/"'\'']*\).*#\1#p' "$DASH/.env" | tail -1)"
fi
if [[ -n "$SITE_HOST" ]]; then
    HEALTH_URL="${HEALTH_URL:-https://$SITE_HOST/healthz}"
    CURL_TO=(--resolve "$SITE_HOST:443:127.0.0.1")
else
    HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/healthz}"
    CURL_TO=()
fi

OLD="$(git_app rev-parse HEAD)"
log "now at $(git_app log --oneline -1)"

# --- 1. the new commits
if [[ -n "$BUNDLE" ]]; then
    [[ -r "$BUNDLE" ]] || die "bundle $BUNDLE not readable"
    git_app bundle verify -q "$BUNDLE" >/dev/null 2>&1 || die "the bundle does not apply to this clone (made from another commit?)"
    head="$(git -C "$APP_DIR" bundle list-heads "$BUNDLE" | awk 'NR==1 {print $2}')"
    git_app fetch -q "$BUNDLE" "+$head:refs/remotes/origin/main"
else
    git_app fetch -q origin
fi
NEW="$(git_app rev-parse "$REF")"
[[ "$NEW" != "$OLD" ]] || { log "already at $REF: nothing to update"; exit 0; }
git_app merge-base --is-ancestor "$OLD" "$NEW" || die "$REF is not ahead of the running commit (fast-forward only)"
log "updating to $(git_app log --oneline -1 "$NEW") ($(git_app rev-list --count "$OLD..$NEW") commits)"
changed() { git_app diff --quiet "$OLD" "$NEW" -- "$@" && return 1 || return 0; }

# --- 2. backup, unless a nightly run holds the database
if systemctl is-active --quiet laqta-nightly.service 2>/dev/null; then
    die "the nightly run is active: wait for it to finish (systemctl status laqta-nightly.service)"
fi
if systemctl cat laqta-backup.service >/dev/null 2>&1; then
    log "backup"
    systemctl start laqta-backup.service || die "the backup failed: nothing was changed (journalctl -u laqta-backup)"
else
    echo "    (no laqta-backup.service: skipped)"
fi

# --- 3. from here on a failure puts the old commit back
rolled_back=0
rollback() {
    local code=$?
    (( rolled_back )) && exit "$code"
    rolled_back=1
    echo "update.sh: FAILED (exit $code): going back to $OLD" >&2
    git_app reset -q --hard "$OLD" || true
    artisan optimize:clear >/dev/null 2>&1 || true
    artisan up >/dev/null 2>&1 || true
    echo "update.sh: back on $(git_app log --oneline -1). The database keeps any added columns (the old code runs on them)." >&2
    exit "$code"
}
trap rollback ERR

log "maintenance on"
artisan down --retry=15 >/dev/null

git_app merge -q --ff-only "$NEW"

if changed requirements.txt; then
    log "requirements.txt changed: pip install"
    as_app "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
fi
if changed dashboard/composer.lock; then
    log "composer.lock changed: composer install"
    (cd "$DASH" && as_app composer install --no-dev --no-interaction --optimize-autoloader --quiet)
fi

log "database: Laravel migrations, then the Python tables"
artisan migrate --force
(cd "$APP_DIR" && as_app "$VENV_PY" -X utf8 -c \
    'import config, local_cache_db, google_sheets; assert local_cache_db.ensure_schema(); google_sheets.SQLiteTransactionQueue(); print("    python tables ready")')

log "caches"
chown -R "$APP_USER:$APP_USER" "$DASH/storage" "$DASH/bootstrap/cache"
artisan optimize:clear >/dev/null
artisan route:cache >/dev/null
artisan view:cache >/dev/null

log "maintenance off"
artisan up >/dev/null

# --- 4. does the new version answer?
log "health check ($HEALTH_URL)"
code=""
for _ in 1 2 3 4 5; do
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 "${CURL_TO[@]}" "$HEALTH_URL" || true)"
    [[ "$code" == "200" || "$code" == "503" ]] && break   # 503 = healthz answered (a check like the nightly is not ok)
    sleep 3
done
[[ "$code" == "200" || "$code" == "503" ]] || { echo "    /healthz answered ${code:-nothing}" >&2; false; }
echo "    /healthz answered $code"

trap - ERR
log "done: $(git_app log --oneline -1)"
echo "    the previous version was $OLD (to go back: docs/deploy_ubuntu.md section 14)"
