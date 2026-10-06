#!/usr/bin/env bash
# Daily MariaDB backup: one gzip dump per run, the newest KEEP_DAYS days kept.
# Installed by install.sh as /usr/local/sbin/laqta-backup and run by laqta-backup.timer as root.
#
# Credentials are never in the repo or in this script: the dump reads root's ~/.my.cnf (mode 600), see
# docs/deploy_ubuntu.md for how to create it. Settings (all optional, from the environment or /etc/default/laqta-backup):
#   DB_NAME      database to dump            (default automation_db)
#   BACKUP_DIR   where the dumps go          (default /var/backups/laqta)
#   KEEP_DAYS    how many days to keep       (default 14)
#   MYSQL_DEFAULTS_FILE  another credentials file instead of ~/.my.cnf
#
# The dump contains the system_settings table, i.e. the API keys saved from the dashboard: the folder is 700 and
# every file 600. Do not copy dumps off the server unencrypted.
set -euo pipefail
umask 077

DB_NAME="${DB_NAME:-automation_db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/laqta}"
KEEP_DAYS="${KEEP_DAYS:-14}"
MYCNF="${MYSQL_DEFAULTS_FILE:-${HOME:-/root}/.my.cnf}"

die() { echo "laqta-backup: $*" >&2; exit 1; }

[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || die "DB_NAME may contain only letters, digits and _"
[[ "$KEEP_DAYS" =~ ^[0-9]+$ && "$KEEP_DAYS" -ge 1 ]] || die "KEEP_DAYS must be a whole number >= 1"

[[ -f "$MYCNF" ]] || die "credentials file $MYCNF not found (create it: see docs/deploy_ubuntu.md)"
perms="$(stat -c '%a' "$MYCNF")"
if (( 8#$perms & 8#077 )); then
    die "$MYCNF is readable by other users (mode $perms): run chmod 600 $MYCNF"
fi

DUMP="$(command -v mariadb-dump || command -v mysqldump || true)"
[[ -n "$DUMP" ]] || die "mariadb-dump / mysqldump not found (apt install mariadb-client)"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date +%Y-%m-%d_%H%M%S)"
final="$BACKUP_DIR/${DB_NAME}_${stamp}.sql.gz"
part="$final.part"
trap 'rm -f "$part"' EXIT

extra=()
if [[ -n "${MYSQL_DEFAULTS_FILE:-}" ]]; then
    extra=("--defaults-extra-file=$MYSQL_DEFAULTS_FILE")   # must be the first option
fi

"$DUMP" "${extra[@]}" --single-transaction --routines --triggers --default-character-set=utf8mb4 "$DB_NAME" \
    | gzip -9 > "$part"

# A truncated or empty dump must never replace a good backup or count as one.
gzip -t "$part" || die "the dump is not a valid gzip file"
[[ "$(stat -c '%s' "$part")" -gt 200 ]] || die "the dump is suspiciously small"
mv "$part" "$final"
trap - EXIT
echo "laqta-backup: wrote $final ($(stat -c '%s' "$final") bytes)"

# Keep the newest KEEP_DAYS days; only this database's own dump files are ever deleted.
find "$BACKUP_DIR" -maxdepth 1 -type f -name "${DB_NAME}_*.sql.gz" -mmin "+$((KEEP_DAYS * 1440))" -print -delete \
    | sed 's/^/laqta-backup: removed old dump /'
