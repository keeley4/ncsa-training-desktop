# Deploying the harness on cPanel (web.illinois.edu)

Host: `trainingdesktop.web.illinois.edu` (cPanel account `trainingdesktop`). Python 3.12 via "Setup Python App" (Passenger), PostgreSQL on localhost.

| Piece | Where |
|---|---|
| Code | `~/harness` (git checkout of this repo) |
| Virtualenv | `~/virtualenv/harness/3.12` (created by cPanel) |
| Entry point | `~/harness/passenger_wsgi.py` → copy of `deploy/cpanel/passenger_wsgi.py` |
| Database URL | `~/.config/ncsahub/db.env` (0600; password generated on the server, never displayed) |
| Moodle token | `~/.config/ncsahub/moodle_token` (0600; IP-restricted to the server's address) |
| Optional settings | `~/.config/ncsahub/app.env` (e.g. `ALLOWED_ORIGINS=https://keeley4.github.io`) |
| Sync | `deploy/cpanel/sync.sh` by cron, log in `~/logs/harness-sync.log` |

## Update the code

```bash
cd ~/harness && git pull --ff-only
~/virtualenv/harness/3.12/bin/pip install -r requirements.txt a2wsgi
cp deploy/cpanel/passenger_wsgi.py passenger_wsgi.py
touch tmp/restart.txt            # Passenger reloads on the next request
```

## First-time setup (done 2026-11-05)

```bash
git clone https://github.com/keeley4/ncsa-training-desktop.git ~/harness
cloudlinux-selector create --json --interpreter=python --version=3.12 --app-root=harness \
  --domain=trainingdesktop.web.illinois.edu --app-uri=/ --startup-file=passenger_wsgi.py --entry-point=application
# then the "Update the code" steps
crontab: 17 * * * * /bin/bash $HOME/harness/deploy/cpanel/sync.sh
```

## Checks

- `https://trainingdesktop.web.illinois.edu/healthz` → `{"ok": true, ...}` after the first sync
- `https://trainingdesktop.web.illinois.edu/api/v1/tiles`
