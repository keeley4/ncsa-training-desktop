# ADR 0003: Identity: Globus Auth now, provider-agnostic

Status: accepted (PRD v2.3, 2026-10-30); not implemented until Phase 3

## Decision

Both Learn@NCSA (already) and the front door use **Globus Auth** (OIDC). The front door is a relying party only: no local passwords. Issuer, client id, scopes and claim mapping are configuration, so moving to CILogon or the NCSA IAM program's broker is a config change plus a re-link run.

## Matching people across systems (identity crosswalk)

Match keys, in order: Globus identity (Moodle's OAuth2 linked-login table stores it for ~4,400 users), then eppn/NetID, then verified email. Never by name. Ambiguous matches go to an admin review queue.

## Phase 1 impact

None at runtime: the prototype is signed-out only. The contract stubs `/me`, `/me/progress`, `/enrollments` and `/crosswalk/review` as `x-phase: 3`.

## Phase 3 implementation (2026-11-05)

- **Flow:** OIDC authorization code with PKCE (S256) **and** a client secret (Globus confidential client `2d08004e-0033-48ea-8b97-f4cee8ad012e`, project "NCSA Training Desktop"). Scopes `openid profile email` only; no Globus data-access scopes.
- **Routes:** `GET /auth/login?next=/path` → Globus; `GET /auth/callback`; `POST /auth/logout`; `GET /auth` (plain status page for testing); `GET /api/v1/me` (never 401: `{"signedIn": false}` when signed out).
- **ID token:** received straight from the token endpoint over TLS with client authentication, so the signature check is skipped (OIDC Core 3.1.3.7 item 6); issuer, audience, expiry and nonce are checked. Revisit (verify against `https://auth.globus.org/jwk.json`) if tokens ever arrive through the browser.
- **Stored:** user (Globus `sub`, name, email, preferred_username, organization, IdP) and every linked identity from `identity_set` (the crosswalk keys). **No Globus tokens are stored.** Sessions: random 256-bit cookie `__Host-ncsa_session` (HttpOnly, Secure, SameSite=Lax, 12 h); only its SHA-256 is in the database.
- **Open-redirect guard:** `next` must be a same-site path (`/x`, never `//host`).
- **Not yet:** crosswalk to Moodle accounts, review queue, rate limits (land with enroll-on-launch).
