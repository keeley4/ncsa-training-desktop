# ADR 0001: Stack

Status: accepted (2026-11-04)

## Decision

- **Harness:** Python 3.12, FastAPI, PostgreSQL. The Moodle client uses only the standard library.
- **Front door:** plain HTML, CSS and JavaScript with no build step and no third-party scripts.
- **Contract:** OpenAPI 3.1 in `contract/openapi.yaml`. Every published document is validated against it before it can be deployed.

## Why

- The harness is mostly data plumbing (Moodle web services in, contract-shaped JSON out, Postgres in between). Python keeps that short and readable for a small team, and FastAPI gives typed routes and generated docs for free.
- Phase 1 needs one page that reads JSON. A framework would add a toolchain to maintain without adding value yet. Phase 2 (the full desktop UI) may adopt one; the contract keeps that change contained to `web/`.
- No external scripts or fonts means no trackers (PRD §9) and nothing to break offline.

## Consequences

- Phase 2 revisits the front-end choice once the desktop UI's needs (keyboard grid, side sheet, calendar) are clear.
- Moodle-side code (`local_ncsahub`, Phase 5) is PHP regardless.
