-- Live workshops and usage analytics (Phase 4/5 prototype). Idempotent. PostgreSQL 10+.

-- Public workshop listings. Registration happens on the linked form (Webtools/Google); no registrant data here.
CREATE TABLE IF NOT EXISTS workshops (
  id             bigserial PRIMARY KEY,
  source_key     text UNIQUE,                 -- e.g. v1:5 for rows imported from the v1 coordination tool
  offering       text NOT NULL DEFAULT '',    -- offering slug, groups repeats of the same workshop
  title          text NOT NULL,
  summary        text NOT NULL DEFAULT '',
  program_tags   jsonb NOT NULL DEFAULT '[]',
  speakers       text NOT NULL DEFAULT '',
  format         text NOT NULL DEFAULT 'webinar',
  timezone       text NOT NULL DEFAULT 'America/Chicago',
  sessions       jsonb NOT NULL DEFAULT '[]', -- [{"start": "2026-11-10T10:00", "end": "2026-11-10T11:30"}] local wall time
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'scheduled', 'cancelled')),
  register_url   text NOT NULL DEFAULT '',
  calendar_url   text NOT NULL DEFAULT '',
  moodle_url     text NOT NULL DEFAULT '',
  recording_url  text NOT NULL DEFAULT '',
  sequence       integer NOT NULL DEFAULT 0, -- iCalendar SEQUENCE, bumped on every change
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Usage events, aggregate-only: no user id, no IP address. sid is a random per-browser-session id.
CREATE TABLE IF NOT EXISTS usage_events (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  sid        text NOT NULL DEFAULT '',
  signed_in  boolean NOT NULL DEFAULT false,
  kind       text NOT NULL,
  target     text NOT NULL DEFAULT '',
  detail     text NOT NULL DEFAULT '',
  n          integer
);
CREATE INDEX IF NOT EXISTS usage_events_at ON usage_events (at);
CREATE INDEX IF NOT EXISTS usage_events_kind_at ON usage_events (kind, at);

-- Learn@NCSA enrolment counts per catalog course (refreshed by cron; counts only).
CREATE TABLE IF NOT EXISTS course_stats (
  course_id   bigint PRIMARY KEY,
  enrolled    integer NOT NULL,
  checked_at  timestamptz NOT NULL DEFAULT now()
);

-- Learn@NCSA activity feed (local_ncsahub outbox, pulled hourly). Moodle user ids only: no names or emails.
CREATE TABLE IF NOT EXISTS moodle_events (
  id           bigint PRIMARY KEY,          -- outbox id on Learn@NCSA
  kind         text NOT NULL,               -- enrolled | unenrolled | course_completed | activity_completion
  moodle_user  bigint NOT NULL,
  course_id    bigint NOT NULL,
  cm_id        bigint NOT NULL DEFAULT 0,
  state        integer NOT NULL DEFAULT 1,
  at           timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS moodle_events_course_at ON moodle_events (course_id, at);
CREATE INDEX IF NOT EXISTS moodle_events_kind_at ON moodle_events (kind, at);

-- Saved learning paths (From Laptop to Cluster answers), one row per front-door account. Answers are ids
-- from catalog/paths.yaml; no names, emails or free text. Deleted when the learner removes the path.
CREATE TABLE IF NOT EXISTS learner_paths (
  user_id     text PRIMARY KEY,
  work        text NOT NULL,
  scale       text NOT NULL,
  fit         text NOT NULL,
  ex          text NOT NULL DEFAULT '',
  par         text NOT NULL DEFAULT '',
  saved_at    timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
