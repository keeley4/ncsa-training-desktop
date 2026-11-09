# Deploying the harness on cPanel (web.illinois.edu)

Host: `trainingdesktop.web.illinois.edu` (cPanel account `trainingdesktop`). Python 3.12 via "Setup Python App" (Passenger), PostgreSQL on localhost.

| Piece | Where |
|---|---|
| Code | `~/harness` (git checkout of this repo) |
| Virtualenv | `~/virtualenv/harness/3.12` (created by cPanel) |
| Entry point | `~/harness/passenger_wsgi.py` → copy of `deploy/cpanel/passenger_wsgi.py` |
| Database URL | `~/.config/ncsahub/db.env` (0600; password generated on the server, never displayed) |
| Moodle token | `~/.config/ncsahub/moodle_token` (0600; IP-restricted to the server's address) |
| App settings | `~/.config/ncsahub/app.env`: `GLOBUS_CLIENT_ID`, `GLOBUS_CLIENT_SECRET_FILE`, `PUBLIC_BASE_URL` (optional `ALLOWED_ORIGINS`) |
| Globus client secret | `~/.config/ncsahub/globus_client_secret` (0600; created by Chris in the Globus console) |
| Sync | `deploy/cpanel/sync.sh` by cron, log in `~/logs/harness-sync.log` |

## Update the code

```bash
cd ~/harness && git pull --ff-only
~/virtualenv/harness/3.12/bin/pip install -r requirements.txt a2wsgi
cp deploy/cpanel/passenger_wsgi.py passenger_wsgi.py
find . -path ./.git -prune -o -type d -exec chmod 755 {} + -o -type f -exec chmod 644 {} +
(set -a; . ~/.config/ncsahub/db.env; set +a; ~/virtualenv/harness/3.12/bin/python -c \
  'import os; from harness.store import PgStore; PgStore(os.environ["DATABASE_URL"]).migrate()')
touch tmp/restart.txt            # Passenger reloads on the next request
```

## First-time setup (done 2026-11-05)

```bash
umask 022                        # code must be world-readable for Passenger; secrets live in ~/.config (0700)
git clone https://github.com/keeley4/ncsa-training-desktop.git ~/harness
cloudlinux-selector create --json --interpreter=python --version=3.12 --app-root=harness \
  --domain=trainingdesktop.web.illinois.edu --app-uri=/ --startup-file=passenger_wsgi.py --entry-point=application
# `create` OVERWRITES passenger_wsgi.py with a stub that imports itself (RecursionError, "Web application could not be started").
# Always copy ours back afterwards — the "Update the code" steps do this.
crontab: 17 * * * * /bin/bash $HOME/harness/deploy/cpanel/sync.sh
```

## Gotchas seen on 2026-11-05

- Passenger error #2 "error occurred while trying to access ..." → the checkout was mode 700/600. Fix: dirs 755, files 644.
- "Web application could not be started" → cPanel's stub `passenger_wsgi.py`. Fix: copy `deploy/cpanel/passenger_wsgi.py` back and `touch tmp/restart.txt`.
- `/api/docs` is off by design (`openapi_url=None`); the contract is at `/api/v1/openapi.yaml`.
- PostgreSQL server is 10.23 (end of life since 2022); fine for the schema, but raise with the cPanel service.

## Sign-in (Phase 3)

`~/.config/ncsahub/app.env`:

```
GLOBUS_CLIENT_ID=2d08004e-0033-48ea-8b97-f4cee8ad012e
GLOBUS_CLIENT_SECRET_FILE=/home/trainingdesktop/.config/ncsahub/globus_client_secret
PUBLIC_BASE_URL=https://trainingdesktop.web.illinois.edu
MOODLE_URL=https://learn.ncsa.illinois.edu
MOODLE_LOOKUP_TOKEN_FILE=/home/trainingdesktop/.config/ncsahub/moodle_lookup_token
ADMIN_USERNAMES=keeley4@illinois.edu
```

`moodle_lookup_token` (0600) is the IP-restricted token for the "NCSA Hub (user lookup)" service on Learn@NCSA
(account `ncsahub_lookup`, one function: `core_user_get_users_by_field`). It is used at sign-in to find the
person's Learn@NCSA account (identity crosswalk). Without it, sign-in still works and the link shows "unavailable".

Without the secret file, `/auth/login` answers 503 "not configured" and `/api/v1/me` says signed out. Test page: `/auth`.
New tables are created by `PgStore.migrate()` (run after each update; idempotent).

## Checks

- `https://trainingdesktop.web.illinois.edu/healthz` → `{"ok": true, ...}` after the first sync
- `https://trainingdesktop.web.illinois.edu/api/v1/tiles`
