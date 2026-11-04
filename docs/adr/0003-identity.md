# ADR 0003: Identity: Globus Auth now, provider-agnostic

Status: accepted (PRD v2.3, 2026-10-30); not implemented until Phase 3

## Decision

Both Learn@NCSA (already) and the front door use **Globus Auth** (OIDC). The front door is a relying party only: no local passwords. Issuer, client id, scopes and claim mapping are configuration, so moving to CILogon or the NCSA IAM program's broker is a config change plus a re-link run.

## Matching people across systems (identity crosswalk)

Match keys, in order: Globus identity (Moodle's OAuth2 linked-login table stores it for ~4,400 users), then eppn/NetID, then verified email. Never by name. Ambiguous matches go to an admin review queue.

## Phase 1 impact

None at runtime: the prototype is signed-out only. The contract stubs `/me`, `/me/progress`, `/enrollments` and `/crosswalk/review` as `x-phase: 3`.
