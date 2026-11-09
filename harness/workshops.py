"""Live workshops (Phase 4 prototype): public listings, calendar files, and an admin-only editor.

Registration links out to each workshop's existing form (Webtools/Google); this site stores no registrants.

  GET  /api/v1/events?when=upcoming|past|all     scheduled + cancelled workshops (drafts are hidden)
  GET  /api/v1/events/{id}                        one workshop
  GET  /api/v1/events/{id}.ics                    calendar file for one workshop (one VEVENT per session)
  GET  /api/v1/events.ics                         feed: upcoming workshops (and changes in the last 30 days)
  GET  /admin/workshops                           admin list + editor (HTML)
  POST /admin/workshops[/{id}]                    save (form)
  POST /api/v1/admin/workshops/import             JSON list of workshop docs, upserted by source_key
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .admin import _same_origin
from .live_store import FIELDS

FORMATS = {"webinar": "Webinar", "series": "Workshop series", "hands-on": "Hands-on workshop"}
STATUSES = ("draft", "scheduled", "cancelled")
_SESSION_LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*$")
HOST = "trainingdesktop.web.illinois.edu"


def _local(ts: str, tz: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=ZoneInfo(tz))


def public_doc(w: dict[str, Any]) -> dict[str, Any]:
    tz = w.get("timezone") or "America/Chicago"
    sessions = []
    for s in w.get("sessions") or []:
        sessions.append({"start": _local(s["start"], tz).isoformat(), "end": _local(s["end"], tz).isoformat()})
    return {
        "id": w["id"], "offering": w.get("offering") or "", "title": w["title"], "summary": w.get("summary") or "",
        "programTags": w.get("program_tags") or [], "speakers": w.get("speakers") or "",
        "format": w.get("format") or "webinar", "timezone": tz, "sessions": sessions,
        "status": w.get("status"), "registerUrl": w.get("register_url") or None,
        "calendarUrl": w.get("calendar_url") or None, "moodleUrl": w.get("moodle_url") or None,
        "recordingUrl": w.get("recording_url") or None, "updatedAt": w.get("updated_at"),
    }


def _end(w: dict[str, Any]) -> datetime | None:
    tz = w.get("timezone") or "America/Chicago"
    ends = [_local(s["end"], tz) for s in w.get("sessions") or []]
    return max(ends) if ends else None


def _start(w: dict[str, Any]) -> datetime | None:
    tz = w.get("timezone") or "America/Chicago"
    starts = [_local(s["start"], tz) for s in w.get("sessions") or []]
    return min(starts) if starts else None


# ---------------------------------------------------------------- iCalendar (RFC 5545)

def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ics_events(w: dict[str, Any], base_url: str) -> list[str]:
    tz = w.get("timezone") or "America/Chicago"
    stamp = w.get("updated_at") or datetime.now(timezone.utc).isoformat()
    stamp_dt = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
    n = len(w.get("sessions") or [])
    lines: list[str] = []
    for i, s in enumerate(w.get("sessions") or [], start=1):
        title = w["title"] + (f" (session {i} of {n})" if n > 1 else "")
        desc = [w.get("summary") or ""]
        if w.get("register_url"):
            desc.append(f"Register: {w['register_url']}")
        if w.get("speakers"):
            desc.append(f"Speakers: {w['speakers']}")
        desc.append("The Zoom link is sent to registered participants.")
        lines += [
            "BEGIN:VEVENT",
            f"UID:workshop-{w['id']}-{i}@{HOST}",
            f"SEQUENCE:{int(w.get('sequence') or 0)}",
            f"DTSTAMP:{_utc(stamp_dt)}",
            f"DTSTART:{_utc(_local(s['start'], tz))}",
            f"DTEND:{_utc(_local(s['end'], tz))}",
            f"SUMMARY:{_esc(title)}",
            f"DESCRIPTION:{_esc(chr(10).join(d for d in desc if d))}",
            "LOCATION:Online (Zoom)",
            f"URL:{base_url.rstrip('/')}/#/workshops",
            f"STATUS:{'CANCELLED' if w.get('status') == 'cancelled' else 'CONFIRMED'}",
            "END:VEVENT",
        ]
    return lines


def ics_document(workshops: list[dict[str, Any]], base_url: str, name: str) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NCSA//Training front door//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(name)}"]
    for w in workshops:
        lines += ics_events(w, base_url)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ---------------------------------------------------------------- admin form helpers

def sessions_to_text(sessions: list[dict[str, str]]) -> str:
    return "\n".join(f"{s['start'][:10]} {s['start'][11:16]}-{s['end'][11:16]}" for s in sessions or [])


def parse_sessions(text: str) -> list[dict[str, str]]:
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        m = _SESSION_LINE.match(line)
        if not m:
            raise ValueError(f"Could not read the session line “{line.strip()}”. Use YYYY-MM-DD HH:MM-HH:MM.")
        day, a, b = m.groups()
        a, b = a.zfill(5), b.zfill(5)
        if b <= a:
            raise ValueError(f"The session on {day} ends before it starts.")
        datetime.fromisoformat(f"{day}T{a}")  # validates the date
        out.append({"start": f"{day}T{a}", "end": f"{day}T{b}"})
    if not out:
        raise ValueError("Add at least one session.")
    return sorted(out, key=lambda s: s["start"])


def _clean_url(v: str) -> str:
    v = (v or "").strip()
    if v and not v.startswith("https://"):
        raise ValueError(f"Links must start with https:// ({v[:60]}).")
    return v


def validate_doc(d: dict[str, Any]) -> dict[str, Any]:
    out = {k: d[k] for k in FIELDS if k in d}
    if not str(out.get("title", "")).strip():
        raise ValueError("A title is required.")
    if out.get("status", "draft") not in STATUSES:
        raise ValueError("Unknown status.")
    if out.get("format", "webinar") not in FORMATS:
        raise ValueError("Unknown format.")
    try:
        ZoneInfo(out.get("timezone") or "America/Chicago")
    except Exception:  # noqa: BLE001
        raise ValueError("Unknown time zone.") from None
    for k in ("register_url", "calendar_url", "moodle_url", "recording_url"):
        if k in out:
            out[k] = _clean_url(out[k])
    if "sessions" in out:
        out["sessions"] = parse_sessions(sessions_to_text(out["sessions"]))
    if "program_tags" in out:
        out["program_tags"] = [str(t).strip()[:30] for t in out["program_tags"] if str(t).strip()][:12]
    for k in ("title", "summary", "speakers", "offering", "source_key"):
        if k in out:
            out[k] = str(out[k] or "").strip()[: 4000 if k == "summary" else 300]
    return out


# ---------------------------------------------------------------- router

def make_workshops_router(store: Any, current_user: Callable[[Request], dict[str, Any] | None],
                          is_admin: Callable[[dict[str, Any] | None], bool],
                          get_base_url: Callable[[], str | None]) -> APIRouter:
    r = APIRouter()

    def base(request: Request) -> str:
        return get_base_url() or str(request.base_url).rstrip("/")

    def visible(when: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        items = [w for w in store.list_workshops() if w.get("status") != "draft" and w.get("sessions")]
        if when == "upcoming":
            items = [w for w in items if _end(w) and _end(w) >= now]
        elif when == "past":
            items = [w for w in items if _end(w) and _end(w) < now]
        items.sort(key=lambda w: _start(w) or now, reverse=(when == "past"))
        return items

    @r.get("/api/v1/events")
    def list_events(when: str = "upcoming") -> Response:
        if when not in ("upcoming", "past", "all"):
            return JSONResponse({"error": "bad_request", "message": "when must be upcoming, past or all."}, 400)
        return JSONResponse({"items": [public_doc(w) for w in visible(when)]},
                            headers={"Cache-Control": "public, max-age=300"})

    @r.get("/api/v1/events.ics")
    def feed(request: Request) -> Response:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        items = [w for w in visible("all") if (_end(w) and _end(w) >= cutoff)]
        body = ics_document(items, base(request), "NCSA live training workshops")
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=900"})

    def find_public(wid: int) -> dict[str, Any] | None:
        w = store.get_workshop(wid)
        return w if w and w.get("status") != "draft" else None

    @r.get("/api/v1/events/{wid}.ics")
    def one_ics(wid: int, request: Request) -> Response:
        w = find_public(wid)
        if not w:
            return JSONResponse({"error": "not_found", "message": "No such workshop."}, 404)
        slug = re.sub(r"[^a-z0-9]+", "-", w["title"].lower()).strip("-")[:50] or "workshop"
        return Response(ics_document([w], base(request), w["title"]), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{slug}.ics"',
                                 "Cache-Control": "public, max-age=300"})

    @r.get("/api/v1/events/{wid}")
    def one(wid: int) -> Response:
        w = find_public(wid)
        if not w:
            return JSONResponse({"error": "not_found", "message": "No such workshop."}, 404)
        return JSONResponse(public_doc(w))

    # ---------- admin ----------
    def guard(request: Request, write: bool = False) -> Response | None:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        if not is_admin(user):
            return JSONResponse({"error": "forbidden", "message": "Administrators only."}, 403)
        if write and not _same_origin(request, get_base_url()):
            return JSONResponse({"error": "forbidden", "message": "Cross-site request refused."}, 403)
        return None

    @r.post("/api/v1/admin/workshops/import")
    async def import_json(request: Request) -> Response:
        err = guard(request, write=True)
        if err:
            return err
        try:
            docs = json.loads(await request.body())
            assert isinstance(docs, list) and len(docs) <= 500
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad_request", "message": "Send a JSON list of workshops."}, 400)
        saved, problems = [], []
        for i, d in enumerate(docs):
            try:
                doc = validate_doc(d)
                if not doc.get("source_key"):
                    raise ValueError("source_key is required for imports.")
                saved.append(store.save_workshop(doc))
            except ValueError as exc:
                problems.append({"index": i, "message": str(exc)})
        return JSONResponse({"saved": saved, "problems": problems}, status_code=200 if not problems else 207)

    def page(request: Request, body: str, title: str) -> HTMLResponse:
        doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
               '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
               f'<title>{html.escape(title)}</title><style>'
               'body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1b1f24}'
               'table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}'
               'label{display:block;font-weight:600;margin-top:12px}input,textarea,select{font:inherit;width:100%;box-sizing:border-box;padding:6px}'
               'textarea{min-height:90px}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}'
               'button,.btn{font:inherit;padding:6px 14px;margin-top:14px}.muted{color:#5b6573;font-size:14px}'
               '.err{background:#fde8e8;padding:8px 12px;border-radius:6px}.ok{background:#e6f4ea;padding:8px 12px;border-radius:6px}'
               '.tag{font-size:12px;padding:1px 8px;border-radius:99px;background:#eef1f5}'
               '@media(max-width:640px){.row{grid-template-columns:1fr}}</style></head><body>'
               f'{body}<p class="muted"><a href="/coord">Coordinator</a> · <a href="/admin/analytics">Usage analytics</a> · <a href="/admin/review">Account review</a> · <a href="/">Front door</a></p></body></html>')
        return HTMLResponse(doc, headers={"Cache-Control": "no-store"})

    def admin_gate(request: Request) -> Response | None:
        err = guard(request)
        if err is None:
            return None
        if err.status_code == 401:
            return RedirectResponse("/auth/login?next=" + urllib.parse.quote(request.url.path), status_code=303)
        return HTMLResponse("<p>Administrators only.</p>", status_code=403)

    def form_html(w: dict[str, Any] | None, error: str | None = None) -> str:
        w = w or {"status": "draft", "format": "webinar", "timezone": "America/Chicago", "sessions": []}
        v = lambda k: html.escape(str(w.get(k) or ""))  # noqa: E731
        sel = lambda k, opts: "".join(  # noqa: E731
            f'<option value="{o}"{" selected" if w.get(k) == o else ""}>{html.escape(lbl)}</option>' for o, lbl in opts)
        action = f"/admin/workshops/{w['id']}" if w.get("id") else "/admin/workshops"
        return (
            (f'<p class="err" role="alert">{html.escape(error)}</p>' if error else "")
            + f'<form method="post" action="{action}">'
            f'<label for="title">Title</label><input id="title" name="title" required value="{v("title")}">'
            f'<label for="summary">Description (shown on the front door)</label><textarea id="summary" name="summary">{v("summary")}</textarea>'
            '<div class="row">'
            f'<div><label for="status">Status</label><select id="status" name="status">{sel("status", [(s, s.title()) for s in STATUSES])}</select>'
            '<span class="muted">Drafts are hidden from learners.</span></div>'
            f'<div><label for="format">Format</label><select id="format" name="format">{sel("format", list(FORMATS.items()))}</select></div>'
            '</div>'
            f'<label for="sessions">Sessions, one per line (local time)</label><textarea id="sessions" name="sessions" '
            f'placeholder="2026-11-10 10:00-11:30">{html.escape(w.get("sessions_text") or sessions_to_text(w.get("sessions") or []))}</textarea>'
            '<div class="row">'
            f'<div><label for="timezone">Time zone</label><input id="timezone" name="timezone" value="{v("timezone")}"></div>'
            f'<div><label for="speakers">Speakers</label><input id="speakers" name="speakers" value="{v("speakers")}"></div>'
            '</div>'
            f'<label for="register_url">Registration form link</label><input id="register_url" name="register_url" type="url" value="{v("register_url")}">'
            '<div class="row">'
            f'<div><label for="calendar_url">Illinois calendar entry</label><input id="calendar_url" name="calendar_url" type="url" value="{v("calendar_url")}"></div>'
            f'<div><label for="moodle_url">Learn@NCSA course</label><input id="moodle_url" name="moodle_url" type="url" value="{v("moodle_url")}"></div>'
            '</div>'
            f'<label for="recording_url">Recording link (after the event)</label><input id="recording_url" name="recording_url" type="url" value="{v("recording_url")}">'
            '<div class="row">'
            f'<div><label for="program_tags">Program tags (comma-separated)</label><input id="program_tags" name="program_tags" value="{html.escape(", ".join(w.get("program_tags") or []))}"></div>'
            f'<div><label for="offering">Offering (groups repeats)</label><input id="offering" name="offering" value="{v("offering")}"></div>'
            '</div>'
            '<button type="submit">Save</button> <a href="/admin/workshops">Cancel</a></form>')

    @r.get("/admin/workshops", include_in_schema=False)
    def admin_list(request: Request, edit: str | None = None, msg: str | None = None) -> Response:
        gate = admin_gate(request)
        if gate:
            return gate
        if edit is not None:
            w = store.get_workshop(int(edit)) if edit.isdigit() else None
            heading = f"Edit: {w['title']}" if w else "New workshop"
            return page(request, f"<h1>{html.escape(heading)}</h1>{form_html(w)}", heading)
        now = datetime.now(timezone.utc)
        rows = []
        for w in sorted(store.list_workshops(), key=lambda w: _start(w) or now, reverse=True):
            st, en = _start(w), _end(w)
            when = st.strftime("%a %b %-d, %Y %-I:%M %p") if st else "no sessions"
            past = " (past)" if en and en < now else ""
            rows.append(
                f'<tr><td><a href="/admin/workshops?edit={w["id"]}">{html.escape(w["title"])}</a>'
                f'<div class="muted">{html.escape(w.get("speakers") or "")}</div></td>'
                f'<td>{html.escape(when)}{past}<div class="muted">{len(w.get("sessions") or [])} session(s)</div></td>'
                f'<td><span class="tag">{html.escape(w.get("status") or "")}</span></td>'
                f'<td>{"form linked" if w.get("register_url") else "<strong>no form link</strong>"}</td></tr>')
        note = f'<p class="ok" role="status">{html.escape(msg)}</p>' if msg else ""
        body = (f'<h1>Live workshops</h1>{note}<p><a class="btn" href="/admin/workshops?edit=new">Add a workshop</a> · '
                '<a href="/api/v1/events.ics">Public calendar feed</a></p>'
                '<table><thead><tr><th>Workshop</th><th>When</th><th>Status</th><th>Registration</th></tr></thead>'
                f'<tbody>{"".join(rows) or "<tr><td colspan=4>No workshops yet.</td></tr>"}</tbody></table>')
        return page(request, body, "Live workshops (admin)")

    async def save_form(request: Request, wid: int | None) -> Response:
        err = guard(request, write=True)
        if err:
            return err
        form = {k: v[0] for k, v in urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"),
                                                           keep_blank_values=True).items()}
        raw = {k: form.get(k, "") for k in ("title", "summary", "status", "format", "timezone", "speakers",
                                             "register_url", "calendar_url", "moodle_url", "recording_url", "offering")}
        raw["program_tags"] = [t for t in form.get("program_tags", "").split(",")]
        try:
            raw["sessions"] = parse_sessions(form.get("sessions", ""))
            doc = validate_doc(raw)
            if doc.get("status") == "scheduled" and not doc.get("register_url"):
                raise ValueError("A scheduled workshop needs a registration form link (or keep it as a draft).")
        except ValueError as exc:
            w = {**raw, "id": wid, "sessions": [], "sessions_text": form.get("sessions", "")}
            body = f"<h1>{'Edit' if wid else 'New workshop'}</h1>{form_html(w, str(exc))}"
            resp = page(request, body, "Fix the workshop")
            resp.status_code = 422
            return resp
        saved = store.save_workshop(doc, wid)
        return RedirectResponse("/admin/workshops?" + urllib.parse.urlencode({"msg": f"Saved “{doc['title']}” (#{saved})."}),
                                status_code=303)

    @r.post("/admin/workshops", include_in_schema=False)
    async def admin_create(request: Request) -> Response:
        return await save_form(request, None)

    @r.post("/admin/workshops/{wid}", include_in_schema=False)
    async def admin_update(wid: int, request: Request) -> Response:
        if not store.get_workshop(wid):
            return JSONResponse({"error": "not_found"}, 404)
        return await save_form(request, wid)

    return r
