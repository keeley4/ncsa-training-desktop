-- Harness catalog schema (Phase 1). Idempotent: safe to run on every start.
-- Later phases add identities/crosswalk (P3), offerings/events/registrations (P4), statements (P5)
-- as new migration files; nothing here should need to change for them.

CREATE TABLE IF NOT EXISTS sync_runs (
  id           bigserial PRIMARY KEY,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  mode         text NOT NULL CHECK (mode IN ('live', 'fixture')),
  status       text NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
  meta         jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
  id            integer PRIMARY KEY,          -- Moodle category id
  position      integer NOT NULL,
  name          text NOT NULL,
  parent_id     integer,
  course_count  integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tiles (
  id        text PRIMARY KEY,                -- course-34, group-ihpcss, tool-delta-docs
  position  integer NOT NULL,
  type      text NOT NULL CHECK (type IN ('course', 'program', 'live', 'recording', 'tool')),
  title     text NOT NULL,
  doc       jsonb NOT NULL                   -- the full Tile document from the contract
);

CREATE TABLE IF NOT EXISTS shelves (
  id          text PRIMARY KEY,
  position    integer NOT NULL,
  title       text NOT NULL,
  purpose     text NOT NULL DEFAULT '',
  start_here  text REFERENCES tiles (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS shelf_tiles (
  shelf_id  text NOT NULL REFERENCES shelves (id) ON DELETE CASCADE,
  position  integer NOT NULL,
  tile_id   text NOT NULL REFERENCES tiles (id) ON DELETE CASCADE,
  PRIMARY KEY (shelf_id, position),
  UNIQUE (shelf_id, tile_id)
);
