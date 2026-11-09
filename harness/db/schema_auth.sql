-- Sign-in (Phase 3). Idempotent: safe to run on every start. PostgreSQL 10+.
-- No Globus tokens are stored: the harness only needs who signed in.

CREATE TABLE IF NOT EXISTS users (
  id                  bigserial PRIMARY KEY,
  globus_sub          text NOT NULL UNIQUE,     -- Globus account id (primary identity), stable
  name                text NOT NULL DEFAULT '',
  email               text,
  preferred_username  text,                     -- e.g. netid@illinois.edu
  organization        text,
  identity_provider   text,                     -- display name of the IdP used for this login
  created_at          timestamptz NOT NULL DEFAULT now(),
  last_login_at       timestamptz NOT NULL DEFAULT now()
);

-- Every identity linked to the Globus account (identity_set): the crosswalk match keys.
CREATE TABLE IF NOT EXISTS user_identities (
  user_id            bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  sub                text NOT NULL,             -- Globus identity id
  username           text NOT NULL DEFAULT '',  -- e.g. netid@illinois.edu, eppn-shaped for campus IdPs
  email              text,
  identity_provider  text,
  organization       text,
  PRIMARY KEY (user_id, sub)
);
CREATE INDEX IF NOT EXISTS user_identities_username ON user_identities (lower(username));

-- Browser sessions. Only a SHA-256 of the cookie value is stored.
CREATE TABLE IF NOT EXISTS sessions (
  id_hash     text PRIMARY KEY,
  user_id     bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires ON sessions (expires_at);

-- In-flight logins: state -> nonce, PKCE verifier, where to return. Lives a few minutes.
CREATE TABLE IF NOT EXISTS login_requests (
  state       text PRIMARY KEY,
  nonce       text NOT NULL,
  verifier    text NOT NULL,
  next_path   text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Identity crosswalk result per signed-in user (re-checked at each sign-in).
-- Only the Moodle account id, auth type and suspended flag of candidates are kept.
CREATE TABLE IF NOT EXISTS moodle_links (
  user_id     bigint PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
  status      text NOT NULL CHECK (status IN ('linked', 'review', 'none', 'unavailable')),
  moodle_id   bigint,
  method      text,
  reason      text,
  candidates  jsonb NOT NULL DEFAULT '[]',
  checked_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS moodle_links_status ON moodle_links (status);

-- Administrator decisions on review items (Phase 3). An admin decision is not overwritten at later sign-ins.
ALTER TABLE moodle_links ADD COLUMN IF NOT EXISTS decided_by text;
ALTER TABLE moodle_links ADD COLUMN IF NOT EXISTS decided_at timestamptz;
