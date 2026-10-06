#!/usr/bin/env bash
# Run a dashboard-started run: the same two commands ApiController::runAll used to start with nohup, now inside
# laqta-run.service (so a php-fpm restart cannot kill them). Usage: run-launcher.sh APP_DIR   (as the app user)
#
# The request file temp/run_request.json is only a trigger. Its content is never read or executed, and the commands
# below are fixed. The request is TAKEN with one atomic mv: the dashboard may cancel it with the same atomic rename
# when this launcher does not answer in time (then it starts the run itself), so exactly one side wins and a run
# never starts twice.
set -euo pipefail

APP_DIR="${1:?usage: run-launcher.sh APP_DIR}"
[[ "$APP_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "run-launcher: APP_DIR must be an absolute plain path (got: $APP_DIR)" >&2; exit 64; }

REQUEST="$APP_DIR/temp/run_request.json"
TAKEN="$REQUEST.taken"
LOG="$APP_DIR/temp/pipeline.log"
PYTHON="$APP_DIR/.venv/bin/python"
MAX_AGE_SECONDS="${LAQTA_RUN_REQUEST_MAX_AGE:-300}"

cd "$APP_DIR"

# Taken by someone else, or cancelled by the dashboard: nothing to do (not a failure, so the path unit goes quiet).
if ! mv -- "$REQUEST" "$TAKEN" 2>/dev/null; then
    echo "run-launcher: no run request (already taken or cancelled)"
    exit 0
fi

# A request left over from before a reboot or an outage must not start a run now: the dashboard waits seconds, not minutes.
age=$(( $(date +%s) - $(stat -c %Y "$TAKEN") ))
if (( age > MAX_AGE_SECONDS )); then
    echo "run-launcher: ignored a stale run request (${age}s old, the limit is ${MAX_AGE_SECONDS}s)"
    exit 0
fi

[[ -x "$PYTHON" ]] || { echo "run-launcher: $PYTHON not found: run install.sh" >&2; exit 1; }

# From here the output goes to temp/pipeline.log (the dashboard's log viewer reads it); the dashboard already rotated the old one.
exec >>"$LOG" 2>&1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

"$PYTHON" "$APP_DIR/main.py" --enqueue
# exec: the worker becomes this unit's main process, so a stop (kill of its pid, or systemctl stop) reaches it directly.
exec "$PYTHON" -u "$APP_DIR/main.py" --worker --trigger=dashboard
