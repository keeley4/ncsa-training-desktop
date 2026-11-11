# NCSA Training (prototype)

A front door for NCSA training: self-paced courses from [Learn@NCSA](https://learn.ncsa.illinois.edu) (Moodle), arranged as **shelves** of **tiles**, one shelf per learning track. This repo is **Phase 1** of the v0 plan: the API contract, the catalog sync from Moodle, a minimal front door, and the harness API that will run on NCSA infrastructure from Phase 3.

> Prototype. Public course information only. Sign-in, progress, enrollment and live-workshop registration come in later phases.

## How it works

```
Learn@NCSA (Moodle)  --read-only web service-->  harness (Python)  -->  contract-shaped JSON
                                                     |                      |
                                                     |                      +--> GitHub Pages: web/ + api/v1/*.json   (now)
                                                     +--> Postgres --> harness API /api/v1/*                      (Phase 3 host)
```

- `catalog/shelves.yaml` is the only hand-edited input: which courses sit on which shelf, groups (a program's yearly runs as one tile), and optional tools and overrides. Everything else comes from Moodle.
- The build validates every document against `contract/openapi.yaml` and refuses to publish if validation fails or if anything secret-looking appears in the output.
- The front door reads `config.json` to find the API. On Pages: `{"apiBase": "api/v1", "suffix": ".json"}`. Against the harness: `{"apiBase": "https://<host>/api/v1", "suffix": ""}`. Same files, same documents.

## Repo layout

| Path | What |
|---|---|
| `contract/openapi.yaml` | API contract (OpenAPI 3.1). Phase 1 catalog endpoints, later phases as `x-phase` stubs |
| `catalog/shelves.yaml` | Shelf curation for the real site (course ids from Learn@NCSA) |
| `harness/` | Moodle client, catalog builder, contract validation, stores (files, Postgres), API, CLI |
| `web/` | Front door (HTML/CSS/JS, no build step, no third-party scripts): `index.html` (shelves, side sheet, search, filters, My desktop) and `curation.html` (catalog health for curators) |
| `fixtures/` | Synthetic Moodle responses and shelves for tests and the dev stack |
| `dev/` | Local Docker stack: dev Moodle 4.5.15 (PHP 8.3, MariaDB 10.11, production limits), Postgres, API, web |
| `docs/adr/` | Decisions: stack, hosting, identity, event stream, UI model |
| `.github/workflows/` | `ci.yml` (tests), `pages.yml` (sync + publish), `dev-stack.yml` (end-to-end stack check) |

## Common tasks

**Curate shelves:** edit `catalog/shelves.yaml` and open a pull request. After merge, the Pages workflow rebuilds. The build summary (Actions tab) lists warnings, e.g. a public course on no shelf or a shelf naming a course that is hidden.

**Run tests:**

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium       # for the browser + accessibility tests in tests/e2e
pytest -q                                   # set TEST_DATABASE_URL to also test the Postgres store
```

The browser tests drive the front door by keyboard, check routing, search, filters and pins, and run axe accessibility checks in light and dark mode at desktop and phone widths.

**Build from fixtures (no Moodle needed):**

```bash
python -m harness build --mode fixture --shelves fixtures/shelves.yaml --out dist
python -m http.server -d dist 8000          # then open http://localhost:8000
```

**Build from the real site** (needs the read-only token):

```bash
MOODLE_TOKEN=... python -m harness build --mode live --site https://learn.ncsa.illinois.edu --out dist
```

**Run the harness API:**

```bash
STORE=files STORE_PATH=dist/api/v1 uvicorn harness.api:app        # serve a build
STORE=postgres DATABASE_URL=postgresql://... uvicorn harness.api:app
python -m harness sync-db --mode live --site https://learn.ncsa.illinois.edu --database-url postgresql://...
```

**Full local stack** (dev Moodle with synthetic courses, Postgres, API, front door):

```bash
docker compose -f dev/docker-compose.yml up --build
# front door http://localhost:8081 · API http://localhost:8000/api/docs · Moodle http://moodle.localhost:8080
```

## Using the front door

- **Shelves** are learning tracks; each tile opens a **side sheet** with the description, sessions or past years, tracks, and the launch link into Learn@NCSA.
- **Links are shareable:** `#/tile/<id>` opens a sheet, `#/shelf/<id>` shows one track as a grid. The browser Back button closes the sheet.
- **Keyboard:** `/` search · Tab moves between shelves · arrow keys move between tiles (up/down jumps shelves) · Enter opens · Esc closes or clears search.
- **Filter:** "No sign-in only" under search. Every tile says "No sign-in" or "Sign in required". Shelf pages (`#/shelf/<id>`) are deep links for staff; the home page has no "See all" links.
- **My desktop:** pin tiles from the sheet; pins are saved in this browser only (sign-in comes in Phase 3).
- **For curators:** `curation.html` lists build warnings, courses on no track, and courses missing a description or image, with links to edit them in Learn@NCSA.

## GitHub Pages setup

1. Settings → Pages → Source: **GitHub Actions**.
2. Settings → Secrets and variables → Actions → New repository secret **`MOODLE_TOKEN`**: the read-only token for the `NCSA Hub (read-only)` web service on Learn@NCSA.
3. Optional repository variable `MOODLE_URL` (defaults to `https://learn.ncsa.illinois.edu`).
4. Actions → *Publish to GitHub Pages* → Run workflow. It also runs daily and on changes to `catalog/`, `web/`, `harness/` or `contract/`.

## Security notes

- The Moodle service account's role has one capability, `webservice/rest:use`. Moodle therefore returns only what an anonymous visitor sees. The token is stored only as an Actions secret and is sent in POST bodies, never URLs.
- The token cannot be IP-restricted while it runs on GitHub's runners. Replace it with an IP-restricted token when the harness moves to an NCSA host (see `docs/adr/0002-hosting-and-portability.md`).
- Course contacts (teacher names) and any user data are dropped by the build. Text is rendered with `textContent`, never as HTML.

## Status

Phase 2 of the v0 plan (desktop catalog) is in place; the usability test is deferred. Next: Phase 3 (Globus sign-in, progress, enroll-on-launch) on an NCSA host.
