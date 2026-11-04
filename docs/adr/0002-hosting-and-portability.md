# ADR 0002: Hosting the prototype on GitHub Pages, built to move

Status: accepted (2026-11-04)

## Decision

Phases 1–2 publish a static site to GitHub Pages from a public repo on Chris Keeley's account. From Phase 3 (sign-in, enrollment, registration) the same front door and the harness API move to an NCSA host.

- A scheduled GitHub Action syncs the public catalog from Learn@NCSA with a read-only web-service token (an Actions secret), validates it against the contract and deploys `web/` plus `api/v1/*.json`.
- The front door reads `config.json`: `{"apiBase": "api/v1", "suffix": ".json"}` on Pages, `{"apiBase": "https://<host>/api/v1", "suffix": ""}` against the harness. Static files and API responses are byte-for-byte the same documents (tested).
- No GitHub-specific code in `harness/` or `web/`. Pages is only a deploy target.

## Guardrails

- **Public data only.** The token's role has a single capability, `webservice/rest:use`, so Moodle returns only what an anonymous visitor can see: visible courses and visible categories. The build also drops courses in hidden categories, course contacts and anything about users.
- **No secrets in output.** The build refuses to publish if the token, `wstoken`, or a `/webservice/` URL appears in any output file.
- **Prototype labeling.** Banner on every page and `noindex`.

## Trade-offs accepted

- GitHub's runners use changing IP addresses, so this token cannot be IP-restricted. It is read-only and sees only public data. At Phase 3, issue a new IP-restricted token on the NCSA host and revoke this one.
- A personal account is a single point of failure. Transfer the repo to an NCSA GitHub organization before Phase 3 (GitHub keeps redirects).
- Scheduled workflows in public repos are paused by GitHub after 60 days without repository activity; re-enable from the Actions tab if that happens.

## Moving hosts (Phase 3)

1. Run the harness container on the NCSA host with `STORE=postgres` and a scheduled `python -m harness sync-db`.
2. Serve `web/` from the same host with `config.json` pointing at the API.
3. New token with Phase 3 functions and an IP restriction; revoke the Pages token; disable the Pages workflow or keep it as a public mirror.
