#!/usr/bin/env bash
# Daily MariaDB backup: one gzip dump per run, optionally encrypted with age and copied offsite with rclone,
# the newest KEEP_DAYS days kept. Installed by install.sh as /usr/local/sbin/laqta-backup and run by
# laqta-backup.timer as root.
#
# Credentials are never in the repo or in this script: the dump reads root's ~/.my.cnf (mode 600), see
# docs/deploy_ubuntu.md for how to create it. Settings (all optional, from the environment or /etc/default/laqta-backup):
#   DB_NAME                  database to dump                (default automation_db)
#   BACKUP_DIR               where the dumps go              (default /var/backups/laqta)
#   KEEP_DAYS                how many days to keep           (default 14)
#   MYSQL_DEFAULTS_FILE      another credentials file instead of ~/.my.cnf
#   BACKUP_AGE_RECIPIENT     encrypt every dump with age for this PUBLIC key (age1...), or a recipients file (/path).
#                            The matching private key never lives on this server. Without it the dump is stored
#                            unencrypted and every run prints a warning.
#   BACKUP_RCLONE_REMOTE     also copy each dump to this rclone remote (for example b2:laqta-backups/db). An
#                            unencrypted dump is NOT sent offsite unless BACKUP_RCLONE_ALLOW_PLAINTEXT=1.
#
# The dump contains the system_settings table, i.e. the API keys saved from the dashboard: the folder is 700 and
# every file 600. With age the readable (gzip) dump exists only for the seconds the encryption takes, inside that
# folder, and is shredded afterwards; the file that stays is NAME_DATE.sql.gz.age. Never copy dumps off the server
# unencrypted. A failed offsite copy is an error (the unit fails and the alert goes out), but the local dump and the
# cleanup of old dumps still happen first.
set -euo pipefail
umask 077

DB_NAME="${DB_NAME:-automation_db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/laqta}"
KEEP_DAYS="${KEEP_DAYS:-14}"
MYCNF="${MYSQL_DEFAULTS_FILE:-${HOME:-/root}/.my.cnf}"
AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
RCLONE_REMOTE="${BACKUP_RCLONE_REMOTE:-}"
ALLOW_PLAIN_OFFSITE="${BACKUP_RCLONE_ALLOW_PLAINTEXT:-0}"

die() { echo "laqta-backup: $*" >&2; exit 1; }
warn() { echo "laqta-backup: WARNING: $*" >&2; }

[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || die "DB_NAME may contain only letters, digits and _"
[[ "$KEEP_DAYS" =~ ^[0-9]+$ && "$KEEP_DAYS" -ge 1 ]] || die "KEEP_DAYS must be a whole number >= 1"

[[ -f "$MYCNF" ]] || die "credentials file $MYCNF not found (create it: see docs/deploy_ubuntu.md)"
perms="$(stat -c '%a' "$MYCNF")"
if (( 8#$perms & 8#077 )); then
    die "$MYCNF is readable by other users (mode $perms): run chmod 600 $MYCNF"
fi

DUMP="$(command -v mariadb-dump || command -v mysqldump || true)"
[[ -n "$DUMP" ]] || die "mariadb-dump / mysqldump not found (apt install mariadb-client)"

# ---- encryption and offsite settings are checked BEFORE anything is dumped: a typo must not give a plaintext backup.
encrypted=0
age_args=()
if [[ -n "$AGE_RECIPIENT" ]]; then
    command -v age >/dev/null 2>&1 || die "BACKUP_AGE_RECIPIENT is set but age is not installed (apt install age)"
    if [[ "$AGE_RECIPIENT" == /* ]]; then
        [[ -f "$AGE_RECIPIENT" && -r "$AGE_RECIPIENT" ]] || die "BACKUP_AGE_RECIPIENT file $AGE_RECIPIENT is not a readable file"
        age_args=(-R "$AGE_RECIPIENT")
    elif [[ "$AGE_RECIPIENT" =~ ^age1[0-9a-z]{58}$ ]]; then
        age_args=(-r "$AGE_RECIPIENT")
    else
        die "BACKUP_AGE_RECIPIENT must be an age PUBLIC key (starts with age1, 62 characters) or the path of a recipients file. Never put the private key (AGE-SECRET-KEY-...) on the server"
    fi
    encrypted=1
else
    warn "BACKUP_AGE_RECIPIENT is not set: this dump holds the API keys saved from the dashboard (system_settings) and is stored UNENCRYPTED on this disk."
    warn "Encrypt it: create a key on YOUR computer (age-keygen -o laqta-backup.key), put its public line (age1...) in /etc/default/laqta-backup as BACKUP_AGE_RECIPIENT=..., keep the key file offline. See docs/deploy_ubuntu.md section 9."
fi
if [[ -n "$RCLONE_REMOTE" ]]; then
    command -v rclone >/dev/null 2>&1 || die "BACKUP_RCLONE_REMOTE is set but rclone is not installed (install.sh --with-rclone, or apt install rclone)"
    # the name must not start with "-" (rclone would read it as an option)
    [[ "$RCLONE_REMOTE" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]*:[A-Za-z0-9_./-]*$ ]] \
        || die "BACKUP_RCLONE_REMOTE must look like remotename:folder (for example b2:laqta-backups/db)"
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date +%Y-%m-%d_%H%M%S)"
plain="$BACKUP_DIR/${DB_NAME}_${stamp}.sql.gz"
plain_part="$plain.part"
if (( encrypted )); then
    final="$plain.age"
else
    final="$plain"
fi
final_part="$final.part"
# whatever stage fails, no half-written or readable leftover stays behind
cleanup() {
    rm -f "$final_part"
    if (( encrypted )); then shred -n 1 -u "$plain_part" 2>/dev/null || rm -f "$plain_part"; else rm -f "$plain_part"; fi
}
trap cleanup EXIT

extra=()
if [[ -n "${MYSQL_DEFAULTS_FILE:-}" ]]; then
    extra=("--defaults-extra-file=$MYSQL_DEFAULTS_FILE")   # must be the first option
fi

"$DUMP" "${extra[@]}" --single-transaction --routines --triggers --default-character-set=utf8mb4 "$DB_NAME" \
    | gzip -9 > "$plain_part"

# A truncated or empty dump must never replace a good backup or count as one.
gzip -t "$plain_part" || die "the dump is not a valid gzip file"
[[ "$(stat -c '%s' "$plain_part")" -gt 200 ]] || die "the dump is suspiciously small"

if (( encrypted )); then
    age "${age_args[@]}" -o "$final_part" "$plain_part" || die "age failed to encrypt the dump"
    [[ "$(head -c 21 "$final_part")" == "age-encryption.org/v1" ]] || die "the encrypted file does not look like an age file"
    [[ "$(stat -c '%s' "$final_part")" -gt 200 ]] || die "the encrypted dump is suspiciously small"
    mv "$final_part" "$final"
    shred -n 1 -u "$plain_part" 2>/dev/null || rm -f "$plain_part"
else
    mv "$plain_part" "$final"
fi
trap - EXIT
note=""
if (( encrypted )); then note=", encrypted with age"; fi
echo "laqta-backup: wrote $final ($(stat -c '%s' "$final") bytes$note)"

# ---- offsite copy: never an unencrypted dump (it holds API keys) unless the owner says so explicitly.
offsite_error=""
if [[ -n "$RCLONE_REMOTE" ]]; then
    if (( ! encrypted )) && [[ "$ALLOW_PLAIN_OFFSITE" != "1" ]]; then
        offsite_error="not copied to $RCLONE_REMOTE: the dump is not encrypted and holds API keys. Set BACKUP_AGE_RECIPIENT (docs section 9)"
    elif rclone copyto --retries 3 --low-level-retries 5 --contimeout 60s --timeout 10m \
            "$final" "${RCLONE_REMOTE%/}/$(basename "$final")"; then
        echo "laqta-backup: copied $(basename "$final") to $RCLONE_REMOTE"
    else
        offsite_error="the copy to $RCLONE_REMOTE failed (the local dump is kept): check  rclone lsd ${RCLONE_REMOTE%%:*}:"
    fi
fi

# Keep the newest KEEP_DAYS days; only this database's own dump files (dated NAME_YYYY-MM-DD_HHMMSS.sql.gz, plain or
# .age, and a stale .part from a killed run) are ever deleted. DB_NAME was validated above, so it is safe in the pattern.
find "$BACKUP_DIR" -maxdepth 1 -type f -regextype posix-extended \
    -regex ".*/${DB_NAME}_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}\.sql\.gz(\.age)?(\.part)?" \
    -mmin "+$((KEEP_DAYS * 1440))" -print -delete \
    | sed 's/^/laqta-backup: removed old dump /'

[[ -z "$offsite_error" ]] || die "$offsite_error"
