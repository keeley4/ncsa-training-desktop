"""Coordinator tools (Phase 4): the v1 Training Coordination Tool's workflow, on the site.

Per workshop: the 12-step checklist with due dates computed from the first session, a copy block per
channel (newsletters, ACCESS calendar, user lists, calendar entry, reminder, evaluation invite) and the
closeout numbers. Offerings are templates: description, tags, speakers, prerequisites and saved copy.
No registrant data is stored here (registrants stay in the v1 tool / the forms themselves).

Ported from the v1 tool (lib/steps.js, lib/comms.js, lib/model.js, 2026-09-14).
Storage is plain SQL with JSON in text columns so the same code runs on PostgreSQL (host) and SQLite (tests).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_SQL = Path(__file__).resolve().parent / "db" / "schema_coord.sql"

# ---------------------------------------------------------------- the 12 steps (v1 lib/steps.js)

STEPS: list[dict[str, Any]] = [
    {"key": "reg_form", "no": 1, "offset": -14, "name": "Set up the registration form",
     "how": "Use Webtools forms. If this event has been offered previously, copy the existing form and update it.",
     "link": "https://forms.illinois.edu/", "link_label": "Illinois Webtools Forms", "field": "register_url",
     "driver": "field"},
    {"key": "calendar", "no": 2, "offset": -14, "name": "Set up the calendar entry",
     "how": "Use Webtools Calendar (NCSA Research Consulting Training Events). Copy the previous entry and update it; "
            "the description block below is last time's text with the dates changed.",
     "link": "https://calendars.illinois.edu/list/7026", "link_label": "Training Events calendar (7026)",
     "field": "calendar_url", "channels": ["webtools_calendar"], "driver": "field"},
    {"key": "advertise", "no": 3, "offset": -14, "name": "Advertise",
     "how": "One announcement goes to Eweek, GradLINKS and Bytes & Pieces; ACCESS, Delta and Campus Cluster lists get "
            "their own version when the tags apply. Done when every channel is marked sent.",
     "link": "https://support.access-ci.org/events", "link_label": "ACCESS training calendar", "driver": "advertise"},
    {"key": "zoom", "no": 4, "offset": -7, "name": "Create the Zoom session",
     "how": "Add the event to your Outlook calendar with a Zoom session; add a waiting room and alternative hosts. "
            "The reminder email needs the join link.",
     "link": "https://illinois.zoom.us/meeting", "link_label": "Zoom: my meetings", "field": "zoom_link",
     "driver": "field"},
    {"key": "monitor_reg", "no": 5, "offset": -3, "name": "Monitor registrations",
     "how": "Run the submissions report on the registration form in Webtools.",
     "link": "https://forms.illinois.edu/", "link_label": "Webtools Forms: submissions report"},
    {"key": "group_list", "no": 6, "offset": -5, "name": "Set up the registrant email group",
     "how": "Download the submission report, copy the registrant emails, and add them to a Webtools email group.",
     "link": "https://webtools.illinois.edu/", "link_label": "Webtools email groups"},
    {"key": "reminder", "no": 7, "offset": -1, "name": "Send the reminder with the Zoom link",
     "how": "Webtools email to the registrant group, about 8:00 am the day before. Multi-day series get one per day.",
     "link": "https://webtools.illinois.edu/", "link_label": "Webtools email", "channels": ["reminder_email"],
     "driver": "sent"},
    {"key": "conduct", "no": 8, "offset": 0, "name": "Run the event (record the Zoom session)", "how": "Host and record."},
    {"key": "attendance", "no": 9, "offset": 1, "name": "Record attendance",
     "how": "Zoom Reports gives the attendance report; or use the highest participant count seen. Subtract NCSA staff.",
     "link": "https://illinois.zoom.us/account/report/user", "link_label": "Zoom: Reports", "driver": "attendance"},
    {"key": "eval_survey", "no": 10, "offset": 1, "name": "Send the evaluation survey",
     "how": "Create the survey in Webtools Survey (standard or event-specific) and send the invitation below.",
     "link": "https://webtools.illinois.edu/", "link_label": "Webtools Survey", "channels": ["eval_invite"],
     "driver": "sent", "field": "survey_url"},
    {"key": "recording_handoff", "no": 11, "offset": 3, "name": "Upload the recording to Mediaspace",
     "how": "Upload the Zoom recording to Illinois Mediaspace yourself, then paste its Mediaspace link here.",
     "link": "https://mediaspace.illinois.edu/", "link_label": "Illinois Mediaspace", "field": "recording_url",
     "driver": "field"},
    {"key": "moodle_archive", "no": 12, "offset": 7, "name": "Archive materials on Learn@NCSA",
     "how": "Slides, recording link and repository go on the event's Learn@NCSA course page.",
     "link": "https://learn.ncsa.illinois.edu/", "link_label": "Learn@NCSA", "field": "moodle_url", "driver": "field"},
]
STEP_BY_KEY = {s["key"]: s for s in STEPS}

# ---------------------------------------------------------------- channels (v1 lib/comms.js)

CHANNELS: list[dict[str, Any]] = [
    {"key": "bytes_pieces", "label": "NCSA Bytes & Pieces", "group": "advertise", "always": True, "share": "newsletters",
     "how": "Marcom ClickUp request form, Deliverable = \"Bytes & Pieces newsletter post\" (2+ week turnaround).",
     "url": "https://forms.clickup.com/4209367/f/40epq-46151/6C72L142BYGTN5IB90", "lead": 21},
    {"key": "eweek", "label": "Illinois Eweek", "group": "advertise", "always": True, "share": "newsletters",
     "how": "Webtools → Bulletins → Eweek (354456754). Deadline noon Friday; issue Sunday. Must link the calendar entry.",
     "url": "https://webtools.illinois.edu/0.portlet?componentId=48", "lead": 14},
    {"key": "gradlinks", "label": "GradLINKS", "group": "advertise", "always": True, "share": "newsletters", "limit": 600,
     "how": "Webtools → Bulletins → GradLinks (113545734). Deadline noon Friday; 600 characters including links.",
     "url": "https://webtools.illinois.edu/0.portlet?componentId=48", "lead": 14},
    {"key": "access_calendar", "label": "ACCESS training calendar", "group": "advertise",
     "tags": ["ACCESS", "Delta", "DeltaAI", "PSC"], "how": "Post at support.access-ci.org/events.",
     "url": "https://support.access-ci.org/events", "lead": 14},
    {"key": "delta_users", "label": "Delta / DeltaAI user list", "group": "advertise", "tags": ["Delta", "DeltaAI"],
     "how": "Send to Greg Bauer to forward to Delta users.", "url": "mailto:gbauer@illinois.edu", "lead": 10},
    {"key": "icc_users", "label": "Illinois Campus Cluster user list", "group": "advertise", "tags": ["ICC"],
     "how": "Send to Christopher Heller to forward to Campus Cluster users.", "url": "mailto:cheller@illinois.edu",
     "lead": 10},
    {"key": "partner_lists", "label": "Speaker / partner lists", "group": "advertise",
     "tags": ["vendor", "accessibility", "partner"], "how": "Send to the speaker or partner to distribute.",
     "url": "", "lead": 14},
    {"key": "webtools_calendar", "label": "Webtools Calendar description", "group": "setup", "always": True,
     "carry": True, "no_subject": True, "how": "Paste into the Webtools Calendar entry.",
     "url": "https://calendars.illinois.edu/list/7026", "lead": 14},
    {"key": "reminder_email", "label": "Day-before reminder", "group": "registrant", "always": True, "per_day": True,
     "how": "Webtools email to the registrant group, ~8:00 am the day before.", "url": "https://webtools.illinois.edu/",
     "lead": 1},
    {"key": "eval_invite", "label": "Evaluation survey invitation", "group": "registrant", "always": True,
     "how": "Webtools email to the registrant group after the event.", "url": "https://webtools.illinois.edu/",
     "lead": -1},
]
CHANNEL_BY_KEY = {c["key"]: c for c in CHANNELS}

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "newsletters": {
        "subject": "{{title}} — free NCSA training, {{when}}",
        "body": "{{title}} — {{when_long}}, {{time_range}} {{tz}}, online via Zoom. Free and open to Illinois researchers, "
                "staff and students, from NCSA Research Consulting.\n\n{{description}}\n\nPresenter: {{speakers}}\n"
                "Register: {{reg_url}}\nDetails: {{calendar_url}}",
    },
    "access_calendar": {
        "subject": "{{title}}",
        "body": "{{title}}\n{{when_long}} · {{time_range}} {{tz}} · Online (Zoom)\nHost: National Center for "
                "Supercomputing Applications (NCSA), University of Illinois Urbana-Champaign\n\n{{description}}\n\n"
                "Resources covered: {{resources}}\nAudience: {{audience}}\n{{#if prereqs}}Prerequisites: {{prereqs}}\n"
                "{{/if}}Presenter: {{speakers}}\n\nRegistration: {{reg_url}}\nMaterials are archived after the event on "
                "NCSA's Learn@NCSA{{#if moodle_url}}: {{moodle_url}}{{/if}}",
    },
    "delta_users": {
        "subject": "{{title}} — {{when}} (Delta / DeltaAI users)",
        "body": "Greg — please forward to the Delta and DeltaAI user lists.\n\n---\n\n{{title}}\n{{when_long}}, "
                "{{time_range}} {{tz}} · Zoom\n\nYou already have an account and allocation, so this session is aimed "
                "straight at getting more out of the system rather than at getting started.\n\n{{description}}\n\n"
                "{{#if prereqs}}To follow along with the hands-on portion: {{prereqs}}\n\n{{/if}}Presenter: {{speakers}}\n"
                "Register: {{reg_url}}",
    },
    "icc_users": {
        "subject": "{{title}} — {{when}} (Illinois Campus Cluster users)",
        "body": "Christopher — please forward to the Illinois Campus Cluster user list.\n\n---\n\n{{title}}\n{{when_long}}, "
                "{{time_range}} {{tz}} · Online via Zoom · Free\n\nNCSA Research Consulting is running this session as "
                "part of the Illinois Computes training program. You already have Campus Cluster access, so this is "
                "aimed at getting more out of the system rather than at getting an account.\n\n{{description}}\n\n"
                "Presenter: {{speakers}}\nFormat: {{format_label}}, {{duration}}\n{{#if prereqs}}\nTo follow along with "
                "the hands-on portion:\n{{prereqs}}\n{{else}}\nNothing to set up in advance — bring your existing ICC "
                "login.\n{{/if}}\n{{#if day_schedule}}Schedule:\n{{day_schedule}}\n\n{{/if}}Register (required): {{reg_url}}\n"
                "Training calendar: {{calendar_url}}\n\nCan't make it live? The recording and materials are posted "
                "afterward on Learn@NCSA{{#if moodle_url}} ({{moodle_url}}){{/if}}. Questions about the session, or "
                "about the Campus Cluster generally, can come back to NCSA Research Consulting.",
    },
    "partner_lists": {
        "subject": "Please share: {{title}} ({{when}})",
        "body": "Hi — we would be grateful if you would share the following with your community lists.\n\n---\n\n"
                "{{title}}\n{{when_long}} · {{time_range}} {{tz}} · Online\n\n{{description}}\n\n{{#if prereqs}}Before "
                "the session: {{prereqs}}\n\n{{/if}}Registration and details: {{reg_url}}\nQuestions or accommodation "
                "requests: NCSA Research Consulting.",
    },
    "webtools_calendar": {
        "subject": "{{title}}",
        "body": "{{description}}\n\n{{#if prereqs}}Prerequisites:{{prereqs}}\n{{else}}There are no prerequisites for "
                "this workshop.\n{{/if}}{{#if day_schedule}}\nSchedule:\n{{day_schedule}}\n{{/if}}\nRegistration is "
                "required: {{reg_url}}\n{{#if reg_close}}Register by {{reg_close}}.\n{{/if}}",
    },
    "reminder_email": {
        "subject": "Tomorrow: {{title}}{{#if day_label}} — {{day_label}}{{/if}} · Zoom link inside",
        "body": "{{title}}{{#if day_label}} — {{day_label}}{{/if}} is tomorrow, {{day_when}}, {{time_range}} {{tz}}.\n\n"
                "Join Zoom: {{zoom_link}}\n\n{{#if prereqs}}Please make sure you are set up before we start:\n{{prereqs}}\n\n"
                "{{/if}}A few practical notes:\n- Join a couple of minutes early; there is a waiting room.\n- The session "
                "is recorded and archived on Learn@NCSA afterward.\n- Questions during the session go in the Zoom chat.\n\n"
                "See you tomorrow.\n\nNCSA Research Consulting",
    },
    "eval_invite": {
        "subject": "Two minutes? Feedback on {{title}}",
        "body": "Thank you for joining {{title}} on {{when_long}}.\n\nWe use this feedback to decide what to offer next "
                "semester and how to improve the material, so a short response genuinely changes what we build:\n\n"
                "{{survey_url}}\n\nThe recording and materials are on Learn@NCSA{{#if moodle_url}}: {{moodle_url}}{{/if}}\n\n"
                "NCSA Research Consulting\nIllinois Computes training program",
    },
}

PLACEHOLDER = {"zoom_link": "ZOOM LINK NOT SET YET", "reg_url": "REGISTRATION URL NOT SET YET",
               "calendar_url": "CALENDAR URL NOT SET YET", "survey_url": "SURVEY URL NOT SET YET",
               "moodle_url": "LEARN@NCSA URL NOT SET YET", "speakers": "SPEAKER TBD",
               "description": "DESCRIPTION NOT WRITTEN YET"}
RESOURCE_BY_TAG = {"Delta": "NCSA Delta", "DeltaAI": "NCSA DeltaAI", "ICC": "Illinois Campus Cluster",
                   "ACCESS": "ACCESS-allocated resources", "IC": "Illinois Computes resources",
                   "NAIRR": "NAIRR Pilot resources", "PSC": "PSC Bridges-2"}
FORMAT_LABEL = {"webinar": "Online webinar", "series": "Multi-day workshop series", "hands-on": "Hands-on workshop"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_IF = re.compile(r"\{\{#if\s+([\w.]+)\}\}([\s\S]*?)(?:\{\{else\}\}([\s\S]*?))?\{\{/if\}\}")
_TOK = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def template_key(channel: str) -> str:
    return CHANNEL_BY_KEY.get(channel, {}).get("share") or channel


def applicable_channels(tags: list[str]) -> list[dict[str, Any]]:
    t = set(tags or [])
    return [c for c in CHANNELS if c.get("always") or any(x in t for x in c.get("tags", []))]


def render(tpl: str, ctx: dict[str, Any]) -> str:
    if not tpl:
        return ""
    out = _IF.sub(lambda m: m.group(2) if ctx.get(m.group(1)) not in (None, "", False) else (m.group(3) or ""), tpl)
    out = _TOK.sub(lambda m: str(ctx[m.group(1)]) if ctx.get(m.group(1)) not in (None, "")
                   else "[[" + PLACEHOLDER.get(m.group(1), m.group(1).upper() + " MISSING") + "]]", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def fmt_date(iso: str, style: str = "short") -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso[:10])
    if style == "long":
        return f"{DOW[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year}"
    if style == "medium":
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    return f"{d.month}/{d.day}/{d.year}"


def fmt12(t: str) -> str:
    if not t:
        return ""
    h, m = (int(x) for x in t[:5].split(":"))
    ap = "pm" if h >= 12 else "am"
    hh = 12 if h % 12 == 0 else h % 12
    return f"{hh}:{m:02d} {ap}" if m else f"{hh} {ap}"


def tz_label(tz: str, iso: str) -> str:
    if tz != "America/Chicago":
        return tz
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo(tz)).tzname() or "CT"


def duration_label(mins: int) -> str:
    if not mins:
        return ""
    if mins % 60 == 0:
        return f"{mins // 60} hour{'' if mins == 60 else 's'}"
    return f"{mins // 60}h {mins % 60}m"


def build_context(w: dict[str, Any], off: dict[str, Any] | None, det: dict[str, Any], day_index: int = 0) -> dict[str, Any]:
    off = off or {}
    sessions = w.get("sessions") or [{"start": "2000-01-01T10:00", "end": "2000-01-01T11:30"}]
    first, last = sessions[0], sessions[-1]
    multi = len(sessions) > 1
    tags = w.get("program_tags") or off.get("program_tags") or []
    prereqs = det.get("prerequisites") or off.get("prerequisites") or []
    s0, e0 = first["start"], first["end"]
    mins = (datetime.fromisoformat(e0) - datetime.fromisoformat(s0)).seconds // 60
    ctx = {
        "title": w.get("title", ""),
        "when": fmt_date(s0),
        "when_long": (f"{fmt_date(s0, 'medium')} – {fmt_date(last['start'], 'medium')}" if multi else fmt_date(s0, "long")),
        "time_range": f"{fmt12(s0[11:16])}–{fmt12(e0[11:16])}",
        "tz": tz_label(w.get("timezone") or "America/Chicago", s0),
        "duration": duration_label(mins or off.get("typical_minutes") or 0),
        "format_label": FORMAT_LABEL.get(w.get("format") or "webinar", w.get("format") or ""),
        "speakers": w.get("speakers") or off.get("default_speakers") or "",
        "description": (w.get("summary") or off.get("description") or "").strip(),
        "audience": off.get("target_audience") or "",
        "reg_url": w.get("register_url") or "",
        "calendar_url": w.get("calendar_url") or "",
        "zoom_link": det.get("zoom_link") or "",
        "moodle_url": w.get("moodle_url") or off.get("moodle_url") or "",
        "survey_url": det.get("survey_url") or "",
        "reg_close": fmt_date(det["reg_close"], "medium") if det.get("reg_close") else "",
        "prereqs": ("\n" + "\n".join(f"  - {p.get('label', '')}{' (' + p['url'] + ')' if p.get('url') else ''}"
                                     for p in prereqs)) if prereqs else "",
        "day_schedule": "\n".join(f"  Day {i + 1} — {fmt_date(s['start'], 'medium')}, {fmt12(s['start'][11:16])}–"
                                  f"{fmt12(s['end'][11:16])}" for i, s in enumerate(sessions)) if multi else "",
        "resources": ", ".join(RESOURCE_BY_TAG[t] for t in tags if t in RESOURCE_BY_TAG),
        "day_label": "", "day_when": fmt_date(s0, "long"),
    }
    if day_index and day_index <= len(sessions):
        s = sessions[day_index - 1]
        ctx.update(day_label=f"Day {day_index}", day_when=fmt_date(s["start"], "long"),
                   time_range=f"{fmt12(s['start'][11:16])}–{fmt12(s['end'][11:16])}")
    return ctx


def tokenizer(ctx: dict[str, Any]) -> Callable[[str], str]:
    """Swap one delivery's dates, links and title back to tokens so its text can seed the next delivery."""
    keys = ["zoom_link", "reg_url", "calendar_url", "survey_url", "moodle_url", "when_long", "day_when", "when",
            "reg_close", "time_range", "title", "speakers"]
    pairs = [(k, str(ctx.get(k) or "")) for k in keys]

    def tok(text: str) -> str:
        out = text or ""
        for k, v in pairs:
            if len(v) > 3:
                out = out.replace(v, "{{" + k + "}}")
        return out
    return tok


# ---------------------------------------------------------------- dates

def first_day(w: dict[str, Any]) -> str | None:
    s = w.get("sessions") or []
    return s[0]["start"][:10] if s else None


def business_shift(d: date, direction: int) -> date:
    if d.weekday() < 5:
        return d
    if direction < 0:
        return d - timedelta(days=d.weekday() - 4)
    return d + timedelta(days=7 - d.weekday())


def compute_due(start_iso: str, step: dict[str, Any], offsets: dict[str, int] | None = None) -> str:
    off = int((offsets or {}).get(step["key"], step["offset"]))
    d = date.fromisoformat(start_iso) + timedelta(days=off)
    if step["key"] not in ("conduct", "reminder"):
        d = business_shift(d, off)
    return d.isoformat()


# ---------------------------------------------------------------- storage

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


JSON_COLS = {"program_tags", "prerequisites"}


class CoordStore:
    """SQL store over PostgreSQL (dsn) or SQLite (':memory:' for tests)."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn
        if dsn:
            import psycopg
            self._psycopg = psycopg
            self._mem = None
        else:
            self._mem = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem.row_factory = sqlite3.Row
            self.migrate()

    def _q(self, sql: str, params: tuple | list = (), one: bool = False, many: bool = True) -> Any:
        if self._mem is not None:
            cur = self._mem.execute(sql.replace("%s", "?"), params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            self._mem.commit()
        else:
            from psycopg.rows import dict_row
            with self._psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                cur = conn.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
                conn.commit()
        return (rows[0] if rows else None) if one else rows

    def migrate(self) -> None:
        sql = SCHEMA_SQL.read_text()
        if self._mem is not None:
            self._mem.executescript(sql)
        else:
            with self._psycopg.connect(self.dsn) as conn:
                conn.execute(sql)
                conn.commit()

    @staticmethod
    def _decode(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        row = dict(row)
        for k in JSON_COLS:
            if isinstance(row.get(k), str):
                try:
                    row[k] = json.loads(row[k] or "[]")
                except ValueError:
                    row[k] = []
        return row

    # offerings
    OFFERING_FIELDS = ["name", "description", "program_tags", "target_audience", "typical_minutes", "default_speakers",
                       "prerequisites", "moodle_url", "default_format", "retro_notes", "prior_form_id", "archived"]

    def offerings(self, include_archived: bool = False) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM coord_offerings ORDER BY name")
        return [self._decode(r) for r in rows if include_archived or not r.get("archived")]

    def offering(self, slug: str) -> dict[str, Any] | None:
        return self._decode(self._q("SELECT * FROM coord_offerings WHERE slug=%s", (slug,), one=True)) if slug else None

    def save_offering(self, slug: str, doc: dict[str, Any]) -> None:
        vals = {k: (json.dumps(doc[k]) if k in JSON_COLS else doc[k]) for k in self.OFFERING_FIELDS if k in doc}
        vals["updated_at"] = _now()
        if self.offering(slug) is None:
            cols = ["slug", *vals]
            self._q(f"INSERT INTO coord_offerings ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    [slug, *vals.values()])
        else:
            self._q(f"UPDATE coord_offerings SET {', '.join(f'{k}=%s' for k in vals)} WHERE slug=%s", [*vals.values(), slug])

    def template(self, slug: str, key: str) -> dict[str, Any] | None:
        return self._q("SELECT subject, body FROM coord_templates WHERE offering=%s AND channel=%s", (slug, key), one=True)

    def save_template(self, slug: str, key: str, subject: str, body: str) -> None:
        self._q("INSERT INTO coord_templates (offering, channel, subject, body, updated_at) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (offering, channel) DO UPDATE SET subject=excluded.subject, body=excluded.body, "
                "updated_at=excluded.updated_at", (slug, key, subject, body, _now()))

    # per-workshop coordination data
    def details(self, wid: int) -> dict[str, Any]:
        row = self._decode(self._q("SELECT * FROM coord_details WHERE workshop_id=%s", (wid,), one=True))
        return row or {"workshop_id": wid, "zoom_link": "", "survey_url": "", "reg_close": "", "prerequisites": [],
                       "notes": ""}

    def save_details(self, wid: int, doc: dict[str, Any]) -> None:
        cur = self.details(wid)
        merged = {**cur, **doc}
        self._q("INSERT INTO coord_details (workshop_id, zoom_link, survey_url, reg_close, prerequisites, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (workshop_id) DO UPDATE SET zoom_link=excluded.zoom_link, "
                "survey_url=excluded.survey_url, reg_close=excluded.reg_close, prerequisites=excluded.prerequisites, "
                "notes=excluded.notes",
                (wid, merged.get("zoom_link") or "", merged.get("survey_url") or "", merged.get("reg_close") or "",
                 json.dumps(merged.get("prerequisites") or []), merged.get("notes") or ""))

    def tasks(self, wid: int) -> dict[str, dict[str, Any]]:
        return {r["step_key"]: r for r in self._q("SELECT * FROM coord_tasks WHERE workshop_id=%s", (wid,))}

    def all_tasks(self) -> dict[int, dict[str, dict[str, Any]]]:
        out: dict[int, dict[str, dict[str, Any]]] = {}
        for r in self._q("SELECT * FROM coord_tasks"):
            out.setdefault(int(r["workshop_id"]), {})[r["step_key"]] = r
        return out

    def save_task(self, wid: int, key: str, **kw: Any) -> None:
        cur = self.tasks(wid).get(key) or {"status": "open", "notes": "", "due_date": None, "done_at": None,
                                           "done_by": None}
        m = {**cur, **kw}
        self._q("INSERT INTO coord_tasks (workshop_id, step_key, status, notes, due_date, done_at, done_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (workshop_id, step_key) DO UPDATE SET "
                "status=excluded.status, notes=excluded.notes, due_date=excluded.due_date, done_at=excluded.done_at, "
                "done_by=excluded.done_by",
                (wid, key, m["status"], m.get("notes") or "", m.get("due_date"), m.get("done_at"), m.get("done_by")))

    def comms(self, wid: int) -> dict[tuple[str, int], dict[str, Any]]:
        return {(r["channel"], int(r["day_index"])): r
                for r in self._q("SELECT * FROM coord_comms WHERE workshop_id=%s", (wid,))}

    def save_comm(self, wid: int, channel: str, day: int, **kw: Any) -> None:
        cur = self.comms(wid).get((channel, day)) or {"subject": None, "edited_text": None, "sent_at": None,
                                                     "sent_by": None}
        m = {**cur, **kw}
        self._q("INSERT INTO coord_comms (workshop_id, channel, day_index, subject, edited_text, sent_at, sent_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (workshop_id, channel, day_index) DO UPDATE SET "
                "subject=excluded.subject, edited_text=excluded.edited_text, sent_at=excluded.sent_at, "
                "sent_by=excluded.sent_by",
                (wid, channel, day, m.get("subject"), m.get("edited_text"), m.get("sent_at"), m.get("sent_by")))

    def forget_workshop(self, wid: int) -> None:
        for t in ("coord_details", "coord_tasks", "coord_comms", "coord_outcomes"):
            self._q(f"DELETE FROM {t} WHERE workshop_id=%s", (wid,))

    def outcome(self, wid: int) -> dict[str, Any]:
        return self._q("SELECT * FROM coord_outcomes WHERE workshop_id=%s", (wid,), one=True) or {}

    def outcomes(self) -> dict[int, dict[str, Any]]:
        return {int(r["workshop_id"]): r for r in self._q("SELECT * FROM coord_outcomes")}

    def save_outcome(self, wid: int, doc: dict[str, Any]) -> None:
        m = {**self.outcome(wid), **doc}
        self._q("INSERT INTO coord_outcomes (workshop_id, registered, peak, staff, eval_responses, eval_summary, "
                "retro_notes, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (workshop_id) DO UPDATE "
                "SET registered=excluded.registered, peak=excluded.peak, staff=excluded.staff, "
                "eval_responses=excluded.eval_responses, eval_summary=excluded.eval_summary, "
                "retro_notes=excluded.retro_notes, updated_at=excluded.updated_at",
                (wid, m.get("registered"), m.get("peak"), m.get("staff"), m.get("eval_responses"),
                 m.get("eval_summary") or "", m.get("retro_notes") or "", _now()))


# ---------------------------------------------------------------- workflow view model

def net_attendance(o: dict[str, Any]) -> int | None:
    if o.get("peak") is None:
        return None
    return max(0, int(o["peak"]) - int(o.get("staff") or 0))


class Workflow:
    """Everything the coordinator page needs for one workshop, computed fresh (nothing generated is stored)."""

    def __init__(self, coord: CoordStore, live: Any):
        self.coord, self.live = coord, live

    def channels_for(self, w: dict[str, Any]) -> list[dict[str, Any]]:
        off = self.coord.offering(w.get("offering") or "") or {}
        return applicable_channels(w.get("program_tags") or off.get("program_tags") or [])

    def previous_delivery(self, w: dict[str, Any]) -> dict[str, Any] | None:
        if not w.get("offering") or not first_day(w):
            return None
        earlier = [x for x in self.live.list_workshops()
                   if x["id"] != w["id"] and x.get("offering") == w["offering"] and first_day(x)
                   and first_day(x) < first_day(w)]
        return max(earlier, key=first_day) if earlier else None

    def copy(self, w: dict[str, Any], channel: str, day: int = 0) -> dict[str, Any]:
        ch = CHANNEL_BY_KEY[channel]
        off = self.coord.offering(w.get("offering") or "")
        det = self.coord.details(w["id"])
        ctx = build_context(w, off, det, day)
        key = template_key(channel)
        tpl = (self.coord.template(w["offering"], key) if w.get("offering") else None)
        origin = "offering" if tpl else "default"
        tpl = dict(tpl or DEFAULT_TEMPLATES.get(key) or {"subject": "", "body": ""})
        prev = self.previous_delivery(w) if ch.get("carry") and origin == "default" else None
        if prev:
            pc = self.coord.comms(prev["id"]).get((channel, day)) or self.coord.comms(prev["id"]).get((channel, 0))
            ptext = (pc or {}).get("edited_text") or ""
            if ptext.strip():
                pctx = build_context(prev, off, self.coord.details(prev["id"]), day)
                tpl["body"] = tokenizer(pctx)(ptext)
                origin = "previous"
        generated = render(tpl["body"], ctx)
        subject = "" if ch.get("no_subject") else render(tpl.get("subject", ""), ctx).strip()
        row = self.coord.comms(w["id"]).get((channel, day)) or {}
        text = row.get("edited_text") if row.get("edited_text") is not None else generated
        return {"channel": channel, "day": day, "label": ch["label"] + (f" — day {day}" if day else ""),
                "how": ch["how"], "url": ch["url"], "limit": ch.get("limit"), "share": ch.get("share"),
                "subject": row.get("subject") or subject, "text": text, "edited": row.get("edited_text") is not None,
                "origin": origin, "sent_at": row.get("sent_at"), "sent_by": row.get("sent_by"),
                "missing": sorted(set(re.findall(r"\[\[([^\]]+)\]\]", text)))}

    def copies(self, w: dict[str, Any], step_key: str | None = None) -> list[dict[str, Any]]:
        out = []
        n = len(w.get("sessions") or [])
        for ch in self.channels_for(w):
            if step_key == "advertise" and ch["group"] != "advertise":
                continue
            if step_key and step_key != "advertise" and ch["key"] not in STEP_BY_KEY[step_key].get("channels", []):
                continue
            days = list(range(1, n + 1)) if ch.get("per_day") and n > 1 else [0]
            out += [self.copy(w, ch["key"], d) for d in days]
        return out

    def steps(self, w: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
        today = today or date.today()
        start = first_day(w)
        tasks = self.coord.tasks(w["id"])
        det = self.coord.details(w["id"])
        out_rec = self.coord.outcome(w["id"])
        rows = []
        for s in STEPS:
            t = tasks.get(s["key"]) or {}
            due = t.get("due_date") or (compute_due(start, s) if start else None)
            status = t.get("status") or "open"
            auto = None
            if status == "open":
                if s.get("driver") == "advertise":
                    adv = self.copies(w, "advertise")
                    if adv and all(c["sent_at"] for c in adv):
                        auto = "all channels sent"
                elif s.get("driver") == "sent":
                    cs = self.copies(w, s["key"])
                    if cs and all(c["sent_at"] for c in cs):
                        auto = "sent"
                elif s.get("driver") == "attendance" and out_rec.get("peak") is not None:
                    auto = "attendance recorded"
                elif s.get("driver") == "field":
                    if (w.get(s["field"]) or det.get(s["field"])):
                        auto = "link saved"
            done = status == "done" or auto is not None
            badge = ""
            if s["key"] == "advertise":
                adv = self.copies(w, "advertise")
                badge = f"{sum(1 for c in adv if c['sent_at'])}/{len(adv)} sent"
            elif s.get("field"):
                badge = "link set" if (w.get(s["field"]) or det.get(s["field"])) else "no link"
            rows.append({**s, "due": due, "status": "na" if status == "na" else ("done" if done else "open"),
                         "auto": auto, "notes": t.get("notes") or "", "done_by": t.get("done_by"),
                         "overdue": bool(due and not done and status != "na" and due < today.isoformat()),
                         "badge": badge})
        return rows

    def summary(self, w: dict[str, Any], today: date | None = None) -> dict[str, Any]:
        steps = self.steps(w, today)
        open_steps = [s for s in steps if s["status"] == "open"]
        return {"done": sum(1 for s in steps if s["status"] in ("done", "na")), "total": len(steps),
                "next": open_steps[0] if open_steps else None,
                "overdue": [s for s in open_steps if s["overdue"]]}


def due_soon(wf: Workflow, workshops: list[dict[str, Any]], days: int = 7, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    horizon = (today + timedelta(days=days)).isoformat()
    out = []
    cutoff = (today - timedelta(days=14)).isoformat()   # closeout runs to +7 days; older ones are history
    for w in workshops:
        if w.get("status") == "cancelled" or not first_day(w) or first_day(w) < cutoff:
            continue
        for s in wf.steps(w, today):
            if s["status"] == "open" and s["due"] and s["due"] <= horizon:
                out.append({"workshop": w, "step": s})
    return sorted(out, key=lambda x: (x["step"]["due"], x["step"]["no"]))
