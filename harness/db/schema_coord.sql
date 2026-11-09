-- Coordinator tools (Phase 4). Portable SQL (PostgreSQL 10+ and SQLite); JSON lives in text columns.
-- No registrant data: names and emails stay in the registration forms and the v1 tool.

CREATE TABLE IF NOT EXISTS coord_offerings (
  slug              text PRIMARY KEY,
  name              text NOT NULL,
  description       text NOT NULL DEFAULT '',
  program_tags      text NOT NULL DEFAULT '[]',
  target_audience   text NOT NULL DEFAULT '',
  typical_minutes   integer NOT NULL DEFAULT 90,
  default_speakers  text NOT NULL DEFAULT '',
  prerequisites     text NOT NULL DEFAULT '[]',   -- [{"label": ..., "url": ...}]
  moodle_url        text NOT NULL DEFAULT '',
  default_format    text NOT NULL DEFAULT 'webinar',
  retro_notes       text NOT NULL DEFAULT '',
  prior_form_id     text NOT NULL DEFAULT '',
  archived          integer NOT NULL DEFAULT 0,
  updated_at        text
);

-- Copy saved back from a delivery, as tokens ({{title}}, {{when_long}}, ...), per offering and channel.
CREATE TABLE IF NOT EXISTS coord_templates (
  offering    text NOT NULL,
  channel     text NOT NULL,
  subject     text NOT NULL DEFAULT '',
  body        text NOT NULL DEFAULT '',
  updated_at  text,
  PRIMARY KEY (offering, channel)
);

-- Coordinator-only details of a workshop (never shown on the public pages).
CREATE TABLE IF NOT EXISTS coord_details (
  workshop_id    integer PRIMARY KEY,
  zoom_link      text NOT NULL DEFAULT '',
  survey_url     text NOT NULL DEFAULT '',
  reg_close      text NOT NULL DEFAULT '',
  prerequisites  text NOT NULL DEFAULT '[]',
  notes          text NOT NULL DEFAULT ''
);

-- Only steps someone touched get a row; due dates are computed from the first session unless overridden.
CREATE TABLE IF NOT EXISTS coord_tasks (
  workshop_id  integer NOT NULL,
  step_key     text NOT NULL,
  status       text NOT NULL DEFAULT 'open',   -- open | done | na
  notes        text NOT NULL DEFAULT '',
  due_date     text,                           -- override; NULL = computed
  done_at      text,
  done_by      text,
  PRIMARY KEY (workshop_id, step_key)
);

-- Edited copy and sent state; the generated text is always rendered fresh.
CREATE TABLE IF NOT EXISTS coord_comms (
  workshop_id  integer NOT NULL,
  channel      text NOT NULL,
  day_index    integer NOT NULL DEFAULT 0,
  subject      text,
  edited_text  text,
  sent_at      text,
  sent_by      text,
  PRIMARY KEY (workshop_id, channel, day_index)
);

-- Closeout counts (no names).
CREATE TABLE IF NOT EXISTS coord_outcomes (
  workshop_id     integer PRIMARY KEY,
  registered      integer,
  peak            integer,
  staff           integer,
  eval_responses  integer,
  eval_summary    text NOT NULL DEFAULT '',
  retro_notes     text NOT NULL DEFAULT '',
  updated_at      text
);
