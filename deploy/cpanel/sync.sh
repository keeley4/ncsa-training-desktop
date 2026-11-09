#!/bin/bash
# Catalog sync on the cPanel host: Moodle (read-only token) -> Postgres. Run by cron.
# Needs ~/.config/ncsahub/db.env (DATABASE_URL) and ~/.config/ncsahub/moodle_token (0600).
set -euo pipefail
umask 077
APP="${APP:-$HOME/harness}"
PY="${PY:-$HOME/virtualenv/harness/3.12/bin/python}"
CONFIG="$HOME/.config/ncsahub"
LOG="$HOME/logs/harness-sync.log"
mkdir -p "$(dirname "$LOG")"

set -a; . "$CONFIG/db.env"; set +a
export MOODLE_TOKEN_FILE="$CONFIG/moodle_token"
SITE="${MOODLE_URL:-https://learn.ncsa.illinois.edu}"

{
  echo "=== $(date -Is) sync start"
  cd "$APP"
  if "$PY" -m harness sync-db --mode live --site "$SITE"; then
    echo "=== $(date -Is) sync ok"
  else
    echo "=== $(date -Is) sync FAILED"
  fi
  # Learn@NCSA enrolment counts for the admin analytics page (learner-service token), and usage-event retention.
  if [ -f "$CONFIG/app.env" ]; then
    (set -a; . "$CONFIG/app.env"; set +a; "$PY" -m harness course-stats) || echo "=== $(date -Is) course-stats FAILED"
    (set -a; . "$CONFIG/app.env"; set +a; "$PY" -m harness pull-feed) || echo "=== $(date -Is) pull-feed FAILED"
  fi
} >> "$LOG" 2>&1

# keep the log small
tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
