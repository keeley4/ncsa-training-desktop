"""Coordinator pages (server-rendered, coordinators and admins only).

  GET  /coord                       due in the next 7 days, upcoming, new workshop, delivered + closeout
  GET  /coord/w/{id}                one workshop: details and the 12 steps with copy blocks inline
  POST /coord/new                   create a workshop (draft) from an offering
  POST /coord/w/{id}/details        save details (public fields + coordinator-only fields)
  POST /coord/w/{id}/step/{key}     done | undo | na | notes | due | field
  POST /coord/w/{id}/copy/{ch}/{d}  save | sent | unsent | reset | to_offering
  POST /coord/w/{id}/outcome        closeout counts
  GET  /coord/offerings[/{slug}]    offering templates; POST to save
  POST /api/v1/admin/coord/import   v1 offerings + task/comms/outcome state (admin)
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
from datetime import date, datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .admin import _same_origin
from .coord import (CHANNEL_BY_KEY, STEP_BY_KEY, STEPS, CoordStore, Workflow, due_soon, first_day, net_attendance,
                    template_key, tokenizer, build_context)
from .workshops import FORMATS, STATUSES, parse_sessions, sessions_to_text, validate_doc

E = html.escape
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def coordinator_usernames_from_env() -> set[str]:
    return {v.strip().lower() for v in os.environ.get("COORDINATOR_USERNAMES", "").split(",") if v.strip()}


def make_is_coordinator(is_admin: Callable[[Any], bool], get_names: Callable[[], set[str]]) -> Callable[[Any], bool]:
    def check(user: dict[str, Any] | None) -> bool:
        if not user:
            return False
        if is_admin(user):
            return True
        names = {(user.get("preferred_username") or "").lower()} | {
            (i.get("username") or "").lower() for i in user.get("identities") or []}
        names.discard("")
        return bool(names & get_names())
    return check


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip()) if str(v).strip() != "" else None
    except ValueError:
        return None


def _prereq_lines(text: str) -> list[dict[str, str]]:
    out = []
    for line in (text or "").splitlines():
        if line.strip():
            label, _, url = line.partition("|")
            out.append({"label": label.strip()[:200], "url": url.strip()[:300]})
    return out[:10]


def _prereq_text(items: list[dict[str, str]]) -> str:
    return "\n".join(f"{p.get('label', '')}{' | ' + p['url'] if p.get('url') else ''}" for p in items or [])


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5b6573;--line:#dde1e6;--acc:#c8442b;--ok:#1d7a3a;--warn:#b42318;--chip:#eef1f5}
@media(prefers-color-scheme:dark){:root{--bg:#11151c;--card:#1a202a;--text:#e8ecf2;--muted:#a9b3c1;--line:#2f3746;--acc:#f08a6e;--ok:#7fd49a;--warn:#ff9b8f;--chip:#263041}}
*{box-sizing:border-box}body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text);max-width:1040px;margin:0 auto;padding:16px}
a{color:inherit}h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:24px 0 8px}h3{font-size:15px;margin:0}
nav.top{display:flex;gap:14px;flex-wrap:wrap;font-size:14px;margin-bottom:10px}.muted{color:var(--muted)}.small{font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.num{text-align:right;font-variant-numeric:tabular-nums}.chip{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;background:var(--chip)}
.over{color:var(--warn);font-weight:600}.ok{color:var(--ok)}.note{background:var(--chip);border-radius:8px;padding:8px 12px}
label{display:block;font-weight:600;margin-top:10px;font-size:14px}input,textarea,select{font:inherit;width:100%;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--text)}
textarea{min-height:80px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.grid3{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
button,.btn{font:inherit;font-size:14px;padding:5px 12px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer;text-decoration:none;display:inline-block}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
form.inline{display:inline}details.step{border:1px solid var(--line);border-radius:10px;background:var(--card);margin:8px 0}
details.step>summary{list-style:none;cursor:pointer;padding:10px 12px;display:flex;gap:10px;align-items:center}
details.step>summary::-webkit-details-marker{display:none}.dot{width:26px;height:26px;border-radius:50%;border:2px solid var(--muted);display:inline-flex;align-items:center;justify-content:center;font-size:12px;flex:none}
.done .dot{background:var(--ok);border-color:var(--ok);color:#fff}.na .dot{border-style:dashed}.stepbody{padding:0 14px 14px 48px}
.copy{border-top:1px solid var(--line);padding-top:10px;margin-top:12px}.count{font-size:12px}.count.bad{color:var(--warn);font-weight:700}
.miss{color:var(--warn);font-size:13px}@media(max-width:700px){.grid,.grid3{grid-template-columns:1fr}.stepbody{padding-left:14px}}
"""

JS = """
document.addEventListener('click',e=>{const b=e.target.closest('[data-copy]');if(!b)return;e.preventDefault();
const t=document.getElementById(b.dataset.copy);navigator.clipboard.writeText(t.value).then(()=>{const o=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=o,1200)})});
const cnt=t=>{const c=document.getElementById(t.id+'-count');if(!c)return;const lim=+t.dataset.limit;c.textContent=t.value.length+(lim?' / '+lim:'')+' characters';c.classList.toggle('bad',lim&&t.value.length>lim)};
document.querySelectorAll('textarea[data-count]').forEach(t=>{cnt(t);t.addEventListener('input',()=>cnt(t))});
"""


def make_coord_router(coord: CoordStore, live: Any, current_user: Callable[[Request], Any],
                      is_coordinator: Callable[[Any], bool], is_admin: Callable[[Any], bool],
                      get_base_url: Callable[[], str | None]) -> APIRouter:
    r = APIRouter()
    wf = Workflow(coord, live)

    # ---------- helpers ----------
    def gate(request: Request, write: bool = False) -> tuple[Any, Response | None]:
        user = current_user(request)
        if not user:
            if write:
                return None, JSONResponse({"error": "signin_required"}, 401)
            return None, RedirectResponse("/auth/login?next=" + urllib.parse.quote(request.url.path), status_code=303)
        if not is_coordinator(user):
            return None, HTMLResponse("<p>Coordinators only.</p>", status_code=403)
        if write and not _same_origin(request, get_base_url()):
            return None, JSONResponse({"error": "forbidden", "message": "Cross-site request refused."}, 403)
        return user, None

    def who(user: dict[str, Any]) -> str:
        return user.get("preferred_username") or user.get("name") or "coordinator"

    async def form_of(request: Request) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"),
                                                         keep_blank_values=True).items()}

    def page(user: Any, title: str, body: str) -> HTMLResponse:
        nav = ('<nav class="top"><a href="/coord"><strong>Coordinator</strong></a><a href="/coord/offerings">Offerings</a>'
               '<a href="/admin/workshops">Public listings</a>'
               + ('<a href="/admin/analytics">Analytics</a>' if is_admin(user) else '')
               + '<a href="/">Front door</a></nav>')
        doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
               f'initial-scale=1"><meta name="robots" content="noindex"><title>{E(title)}</title><style>{CSS}</style></head>'
               f'<body>{nav}{body}<script>{JS}</script></body></html>')
        return HTMLResponse(doc, headers={"Cache-Control": "no-store"})

    def back(wid: int, anchor: str = "", msg: str = "") -> RedirectResponse:
        q = ("?" + urllib.parse.urlencode({"msg": msg})) if msg else ""
        return RedirectResponse(f"/coord/w/{wid}{q}{'#' + anchor if anchor else ''}", status_code=303)

    def when(w: dict[str, Any]) -> str:
        d = first_day(w)
        if not d:
            return "no date"
        s = w["sessions"][0]["start"][11:16]
        return f"{datetime.fromisoformat(d).strftime('%a %b %-d, %Y')} {s}" + (
            f" (+{len(w['sessions']) - 1} more)" if len(w["sessions"]) > 1 else "")

    def get_w(wid: int) -> dict[str, Any] | None:
        return live.get_workshop(wid)

    # ---------- dashboard ----------
    @r.get("/coord", include_in_schema=False)
    def dashboard(request: Request) -> Response:
        user, err = gate(request)
        if err:
            return err
        today = date.today()
        ws = live.list_workshops()
        up = sorted([w for w in ws if first_day(w) and first_day(w) >= today.isoformat()], key=first_day)
        past = sorted([w for w in ws if first_day(w) and first_day(w) < today.isoformat()
                       and w.get("status") != "cancelled"], key=first_day, reverse=True)
        due = due_soon(wf, [w for w in ws if w.get("status") != "draft" or (first_day(w) or "") >= today.isoformat()],
                       7, today)
        due_rows = "".join(
            f'<tr><td class="{"over" if d["step"]["overdue"] else ""}">{E(d["step"]["due"])}</td>'
            f'<td><a href="/coord/w/{d["workshop"]["id"]}#s-{d["step"]["key"]}">{E(d["workshop"]["title"])}</a></td>'
            f'<td>{d["step"]["no"]}. {E(d["step"]["name"])}</td></tr>' for d in due)
        up_rows = ""
        for w in up:
            s = wf.summary(w, today)
            nxt = s["next"]
            up_rows += (f'<tr><td><a href="/coord/w/{w["id"]}">{E(w["title"])}</a></td><td>{E(when(w))}</td>'
                        f'<td><span class="chip">{E(w.get("status") or "")}</span></td><td class="num">{s["done"]}/{s["total"]}</td>'
                        f'<td>{E(str(nxt["no"]) + ". " + nxt["name"]) if nxt else "all done"}'
                        f'{" <span class=over>(" + str(len(s["overdue"])) + " overdue)</span>" if s["overdue"] else ""}</td></tr>')
        outs = coord.outcomes()
        past_rows = ""
        for w in past[:20]:
            o = outs.get(w["id"], {})
            net = net_attendance(o)
            reg = o.get("registered")
            noshow = f"{round(100 * (1 - net / reg))}%" if net is not None and reg else "—"
            missing = [x for x, ok in (("attendance", o.get("peak") is not None), ("recording", w.get("recording_url")))
                       if not ok]
            past_rows += (f'<tr><td><a href="/coord/w/{w["id"]}">{E(w["title"])}</a></td><td>{E(first_day(w) or "")}</td>'
                          f'<td class="num">{reg if reg is not None else "—"}</td><td class="num">{net if net is not None else "—"}</td>'
                          f'<td class="num">{noshow}</td><td class="num">{o.get("eval_responses") if o.get("eval_responses") is not None else "—"}</td>'
                          f'<td>{"<span class=over>missing " + E(", ".join(missing)) + "</span>" if missing else "<span class=ok>complete</span>"}</td></tr>')
        offs = coord.offerings()
        opts = '<option value="">(no offering — one-off)</option>' + "".join(
            f'<option value="{E(o["slug"])}">{E(o["name"])}</option>' for o in offs)
        msg = request.query_params.get("msg")
        body = (
            '<h1>Workshops</h1>' + (f'<p class="note" role="status">{E(msg)}</p>' if msg else "")
            + '<h2>Due in the next 7 days</h2><div class="card">'
            + (f'<table><thead><tr><th>Due</th><th>Workshop</th><th>Step</th></tr></thead><tbody>{due_rows}</tbody></table>'
               if due_rows else '<p class="muted">Nothing due this week.</p>')
            + '</div><h2>Upcoming</h2><div class="card">'
            + (f'<table><thead><tr><th>Workshop</th><th>When</th><th>Public</th><th class="num">Steps</th><th>Next</th></tr></thead>'
               f'<tbody>{up_rows}</tbody></table>' if up_rows else '<p class="muted">No upcoming workshops.</p>')
            + '</div><h2>Plan a workshop</h2><div class="card"><form method="post" action="/coord/new"><div class="grid">'
            f'<div><label for="n-off">Offering</label><select id="n-off" name="offering">{opts}</select></div>'
            '<div><label for="n-title">Title (blank = offering name)</label><input id="n-title" name="title"></div></div>'
            '<label for="n-sess">Sessions, one per line (local time)</label><textarea id="n-sess" name="sessions" '
            'placeholder="2027-02-16 10:00-11:30" style="min-height:60px"></textarea>'
            '<p class="muted small">Created as a draft (hidden from learners). Due dates for all 12 steps follow the first session.</p>'
            '<button class="primary">Create</button></form></div>'
            '<h2>Delivered</h2><div class="card">'
            + (f'<table><thead><tr><th>Workshop</th><th>Date</th><th class="num">Registered</th><th class="num">Attended (net)</th>'
               f'<th class="num">No-show</th><th class="num">Evals</th><th>Closeout</th></tr></thead><tbody>{past_rows}</tbody></table>'
               if past_rows else '<p class="muted">No delivered workshops yet.</p>')
            + '</div>')
        return page(user, "Coordinator", body)

    @r.post("/coord/new", include_in_schema=False)
    async def create(request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        f = await form_of(request)
        off = coord.offering(f.get("offering", "")) or {}
        try:
            sessions = parse_sessions(f.get("sessions", ""))
            doc = validate_doc({"title": (f.get("title") or off.get("name") or "").strip(), "status": "draft",
                                "format": off.get("default_format") or "webinar", "timezone": "America/Chicago",
                                "summary": off.get("description") or "", "speakers": off.get("default_speakers") or "",
                                "program_tags": off.get("program_tags") or [], "offering": off.get("slug") or "",
                                "moodle_url": off.get("moodle_url") or "", "register_url": "", "calendar_url": "",
                                "recording_url": "", "sessions": sessions})
        except ValueError as exc:
            return RedirectResponse("/coord?" + urllib.parse.urlencode({"msg": str(exc)}), status_code=303)
        wid = live.save_workshop(doc)
        if off.get("prerequisites"):
            coord.save_details(wid, {"prerequisites": off["prerequisites"]})
        return back(wid, msg="Created as a draft. Work through the steps; publish from Details when the form is ready.")

    # ---------- workshop page ----------
    def copy_html(w: dict[str, Any], c: dict[str, Any]) -> str:
        cid = f"c-{c['channel']}-{c['day']}"
        act = f"/coord/w/{w['id']}/copy/{c['channel']}/{c['day']}"
        sent = (f'<span class="ok">Sent {E((c["sent_at"] or "")[:10])}{" by " + E(c["sent_by"]) if c.get("sent_by") else ""}</span>'
                if c["sent_at"] else '<span class="muted">Not sent</span>')
        origin = {"offering": "from this offering's saved wording", "previous": "carried forward from the last delivery",
                  "default": "default wording"}[c["origin"]]
        share = " · shared by Eweek, GradLINKS and Bytes & Pieces" if c.get("share") else ""
        miss = (f'<p class="miss">Still missing: {E(", ".join(c["missing"]))}</p>' if c["missing"] else "")
        subj = (f'<label for="{cid}-s">Subject</label><div class="row" style="margin-top:0"><input id="{cid}-s" name="subject" '
                f'value="{E(c["subject"])}" form="{cid}-f"><button data-copy="{cid}-s" type="button">Copy</button></div>'
                if c["subject"] else "")
        return (
            f'<div class="copy" id="{cid}"><div class="row" style="margin-top:0;justify-content:space-between">'
            f'<h3>{E(c["label"])}</h3>{sent}</div>'
            f'<p class="muted small">{E(c["how"])}{" " if c["url"] else ""}'
            + (f'<a href="{E(c["url"])}" target="_blank" rel="noopener">open ↗</a>' if c["url"] else "")
            + f' · {origin}{"" if not c["edited"] else " (edited)"}{share}</p>{miss}{subj}'
            f'<label for="{cid}-t">Text</label><textarea id="{cid}-t" name="text" form="{cid}-f" data-count '
            f'data-limit="{c["limit"] or ""}" style="min-height:{"220" if len(c["text"]) > 500 else "140"}px">{E(c["text"])}</textarea>'
            f'<div class="count" id="{cid}-t-count"></div>'
            f'<form id="{cid}-f" method="post" action="{act}" class="row">'
            f'<button type="button" data-copy="{cid}-t">Copy text</button>'
            f'<button name="action" value="save">Save edit</button>'
            + (f'<button name="action" value="unsent">Undo sent</button>' if c["sent_at"]
               else f'<button name="action" value="sent" class="primary">Mark sent</button>')
            + (f'<button name="action" value="reset">Reset to template</button>' if c["edited"] else "")
            + (f'<button name="action" value="to_offering">Save wording to offering</button>' if w.get("offering") else "")
            + '</form></div>')

    def shared_html(w: dict[str, Any], group: list[dict[str, Any]]) -> str:
        """One editable announcement for the channels that share a body, then one row per destination."""
        c = group[0]
        cid = f"c-{c['share']}"
        act = f"/coord/w/{w['id']}/copy/{c['channel']}/0"
        limit = min((g["limit"] for g in group if g["limit"]), default=None)
        origin = {"offering": "from this offering's saved wording", "previous": "carried forward from the last delivery",
                  "default": "default wording"}[c["origin"]]
        miss = (f'<p class="miss">Still missing: {E(", ".join(c["missing"]))}</p>' if c["missing"] else "")
        dests = ""
        for g in group:
            ga = f"/coord/w/{w['id']}/copy/{g['channel']}/0"
            sent = (f'<span class="ok">Sent {E((g["sent_at"] or "")[:10])}{" by " + E(g["sent_by"]) if g.get("sent_by") else ""}</span>'
                    if g["sent_at"] else '<span class="muted">Not sent</span>')
            dests += (f'<div class="row" id="c-{g["channel"]}-0" style="justify-content:space-between;border-top:1px dashed var(--line);padding-top:6px">'
                      f'<span><strong>{E(g["label"])}</strong> <span class="muted small">{E(g["how"])} '
                      + (f'<a href="{E(g["url"])}" target="_blank" rel="noopener">open ↗</a>' if g["url"] else "")
                      + f'</span></span><span>{sent} <form class="inline" method="post" action="{ga}">'
                      + (f'<button name="action" value="unsent">Undo sent</button>' if g["sent_at"]
                         else f'<button name="action" value="sent" class="primary">Mark sent</button>')
                      + '</form></span></div>')
        return (
            f'<div class="copy" id="{cid}"><h3>Newsletter announcement</h3>'
            f'<p class="muted small">One text for {E(", ".join(g["label"] for g in group))} · {origin}'
            f'{" (edited)" if c["edited"] else ""}</p>{miss}'
            f'<label for="{cid}-s">Subject</label><div class="row" style="margin-top:0"><input id="{cid}-s" name="subject" '
            f'value="{E(c["subject"])}" form="{cid}-f"><button data-copy="{cid}-s" type="button">Copy</button></div>'
            f'<label for="{cid}-t">Text</label><textarea id="{cid}-t" name="text" form="{cid}-f" data-count '
            f'data-limit="{limit or ""}" style="min-height:180px">{E(c["text"])}</textarea>'
            f'<div class="count" id="{cid}-t-count"></div>'
            f'<form id="{cid}-f" method="post" action="{act}" class="row">'
            f'<button type="button" data-copy="{cid}-t">Copy text</button><button name="action" value="save">Save edit</button>'
            + (f'<button name="action" value="reset">Reset to template</button>' if c["edited"] else "")
            + (f'<button name="action" value="to_offering">Save wording to offering</button>' if w.get("offering") else "")
            + f'</form>{dests}</div>')

    def field_value(w: dict[str, Any], det: dict[str, Any], field: str) -> str:
        return str(w.get(field) or det.get(field) or "")

    @r.get("/coord/w/{wid}", include_in_schema=False)
    def workshop(wid: int, request: Request) -> Response:
        user, err = gate(request)
        if err:
            return err
        w = get_w(wid)
        if not w:
            return HTMLResponse("<p>No such workshop.</p>", status_code=404)
        det = coord.details(wid)
        off = coord.offering(w.get("offering") or "")
        o = coord.outcome(wid)
        steps = wf.steps(w)
        nxt = next((s for s in steps if s["status"] == "open"), None)
        sel = lambda name, cur, opts: "".join(  # noqa: E731
            f'<option value="{k}"{" selected" if cur == k else ""}>{E(v)}</option>' for k, v in opts)
        details = (
            f'<details class="step" id="details"{" open" if request.query_params.get("edit") else ""}><summary>'
            '<span class="dot">i</span><strong>Date, time &amp; details</strong></summary><div class="stepbody">'
            f'<form method="post" action="/coord/w/{wid}/details">'
            f'<label for="d-title">Title</label><input id="d-title" name="title" value="{E(w["title"])}" required>'
            f'<label for="d-sum">Description (public)</label><textarea id="d-sum" name="summary">{E(w.get("summary") or "")}</textarea>'
            '<div class="grid">'
            f'<div><label for="d-st">Public status</label><select id="d-st" name="status">{sel("status", w.get("status"), [(s, s.title()) for s in STATUSES])}</select></div>'
            f'<div><label for="d-fmt">Format</label><select id="d-fmt" name="format">{sel("format", w.get("format"), list(FORMATS.items()))}</select></div></div>'
            f'<label for="d-sess">Sessions (local time)</label><textarea id="d-sess" name="sessions" style="min-height:60px">{E(sessions_to_text(w.get("sessions") or []))}</textarea>'
            '<div class="grid">'
            f'<div><label for="d-sp">Speakers</label><input id="d-sp" name="speakers" value="{E(w.get("speakers") or "")}"></div>'
            f'<div><label for="d-tags">Program tags</label><input id="d-tags" name="program_tags" value="{E(", ".join(w.get("program_tags") or []))}"></div>'
            f'<div><label for="d-reg">Registration form</label><input id="d-reg" name="register_url" value="{E(w.get("register_url") or "")}"></div>'
            f'<div><label for="d-close">Register by (date)</label><input id="d-close" name="reg_close" type="date" value="{E(det.get("reg_close") or "")}"></div>'
            f'<div><label for="d-cal">Calendar entry</label><input id="d-cal" name="calendar_url" value="{E(w.get("calendar_url") or "")}"></div>'
            f'<div><label for="d-mdl">Learn@NCSA course</label><input id="d-mdl" name="moodle_url" value="{E(w.get("moodle_url") or "")}"></div>'
            f'<div><label for="d-zoom">Zoom link (coordinators only)</label><input id="d-zoom" name="zoom_link" value="{E(det.get("zoom_link") or "")}"></div>'
            f'<div><label for="d-svy">Evaluation survey</label><input id="d-svy" name="survey_url" value="{E(det.get("survey_url") or "")}"></div>'
            f'<div><label for="d-rec">Recording</label><input id="d-rec" name="recording_url" value="{E(w.get("recording_url") or "")}"></div>'
            f'<div><label for="d-tz">Time zone</label><input id="d-tz" name="timezone" value="{E(w.get("timezone") or "America/Chicago")}"></div></div>'
            f'<label for="d-pre">Prerequisites, one per line: label | link</label><textarea id="d-pre" name="prerequisites" style="min-height:60px">{E(_prereq_text(det.get("prerequisites") or []))}</textarea>'
            f'<label for="d-notes">Notes (coordinators only)</label><textarea id="d-notes" name="notes" style="min-height:60px">{E(det.get("notes") or "")}</textarea>'
            '<div class="row"><button class="primary">Save details</button></div></form>'
            + (f'<form method="post" action="/coord/w/{wid}/delete" class="row"><label class="small" style="font-weight:400">'
               '<input type="checkbox" name="confirm" value="yes" required style="width:auto"> I understand this permanently '
               'deletes this draft and its checklist</label><button>Delete draft</button></form>'
               if w.get("status") == "draft" else "")
            + '</div></details>')
        step_html = ""
        for s in steps:
            cls = s["status"]
            body = f'<p>{E(s["how"])}</p>'
            if s.get("link"):
                body += f'<p class="small"><a href="{E(s["link"])}" target="_blank" rel="noopener">{E(s["link_label"])} ↗</a></p>'
            if s["key"] == "reg_form" and off and off.get("prior_form_id"):
                body += (f'<p class="small">Last delivery\'s Webtools form id: <strong>{E(off["prior_form_id"])}</strong> — '
                         'open it and use Forms → Copy.</p>')
            if s.get("field"):
                f = s["field"]
                body += (f'<form method="post" action="/coord/w/{wid}/step/{s["key"]}" class="row">'
                         f'<input type="hidden" name="action" value="field"><input name="value" aria-label="{E(f)}" '
                         f'placeholder="Paste the link" value="{E(field_value(w, det, f))}" style="flex:1;min-width:240px">'
                         '<button>Save link</button></form>')
            if s["key"] == "attendance":
                body += (f'<form method="post" action="/coord/w/{wid}/outcome"><div class="grid3">'
                         f'<div><label for="o-reg">Registered</label><input id="o-reg" name="registered" inputmode="numeric" value="{E(str(o.get("registered") if o.get("registered") is not None else ""))}"></div>'
                         f'<div><label for="o-peak">Peak in Zoom</label><input id="o-peak" name="peak" inputmode="numeric" value="{E(str(o.get("peak") if o.get("peak") is not None else ""))}"></div>'
                         f'<div><label for="o-staff">NCSA staff</label><input id="o-staff" name="staff" inputmode="numeric" value="{E(str(o.get("staff") if o.get("staff") is not None else ""))}"></div>'
                         f'<div><label for="o-ev">Evaluations</label><input id="o-ev" name="eval_responses" inputmode="numeric" value="{E(str(o.get("eval_responses") if o.get("eval_responses") is not None else ""))}"></div></div>'
                         f'<p class="small">Net attendance: <strong>{net_attendance(o) if net_attendance(o) is not None else "—"}</strong></p>'
                         f'<label for="o-sum">Evaluation summary</label><textarea id="o-sum" name="eval_summary" style="min-height:60px">{E(o.get("eval_summary") or "")}</textarea>'
                         f'<label for="o-retro">What to change next time</label><textarea id="o-retro" name="retro_notes" style="min-height:60px">{E(o.get("retro_notes") or "")}</textarea>'
                         + ('<label class="small" style="font-weight:400"><input type="checkbox" name="to_offering" value="1" style="width:auto"> '
                            'also add this note to the offering for next time</label>' if w.get("offering") else "")
                         + '<div class="row"><button class="primary">Save closeout</button></div></form>')
            cps = wf.copies(w, s["key"]) if s["key"] in ("advertise", "calendar", "reminder", "eval_survey") else []
            shared = [c for c in cps if c.get("share")]
            if shared:
                body += shared_html(w, shared)
            for c in cps:
                if not c.get("share"):
                    body += copy_html(w, c)
            body += (f'<form method="post" action="/coord/w/{wid}/step/{s["key"]}" class="row">'
                     f'<input name="notes" aria-label="Notes for step {s["no"]}" placeholder="Notes" value="{E(s["notes"])}" style="flex:1;min-width:200px">'
                     '<button name="action" value="notes">Save note</button>'
                     f'<input name="due" type="date" aria-label="Due date for step {s["no"]}" value="{E(s["due"] or "")}" style="width:auto">'
                     '<button name="action" value="due">Change due date</button>'
                     + ('<button name="action" value="undo">Reopen</button>' if s["status"] != "open"
                        else '<button name="action" value="done" class="primary">Mark done</button><button name="action" value="na">Not applicable</button>')
                     + '</form>')
            state = ("done" + (f" ({s['auto']})" if s["auto"] else "")) if s["status"] == "done" else (
                "not applicable" if s["status"] == "na" else ("overdue" if s["overdue"] else "due"))
            step_html += (
                f'<details class="step {cls}" id="s-{s["key"]}"{" open" if nxt and nxt["key"] == s["key"] else ""}><summary>'
                f'<span class="dot">{"✓" if cls == "done" else s["no"]}</span><span style="flex:1"><strong>{E(s["name"])}</strong> '
                f'{"<span class=chip>" + E(s["badge"]) + "</span>" if s["badge"] else ""}</span>'
                f'<span class="small {"over" if s["overdue"] else "muted"}">{E(state)} {E(s["due"] or "")}</span></summary>'
                f'<div class="stepbody">{body}</div></details>')
        s = wf.summary(w)
        msg = request.query_params.get("msg")
        pub = ('<a href="/#/workshops" target="_blank" rel="noopener">public listing ↗</a>' if w.get("status") != "draft"
               else 'draft — hidden from learners')
        body = (
            f'<h1>{E(w["title"])}</h1><p class="muted">{E(when(w))} · <span class="chip">{E(w.get("status") or "")}</span> {pub} · '
            f'{s["done"]}/{s["total"]} steps'
            + (f' · offering: <a href="/coord/offerings/{E(off["slug"])}">{E(off["name"])}</a>' if off else "")
            + '</p>' + (f'<p class="note" role="status">{E(msg)}</p>' if msg else "")
            + details + step_html)
        return page(user, w["title"], body)

    @r.post("/coord/w/{wid}/details", include_in_schema=False)
    async def save_details(wid: int, request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        w = get_w(wid)
        if not w:
            return JSONResponse({"error": "not_found"}, 404)
        f = await form_of(request)
        raw = {k: f.get(k, "") for k in ("title", "summary", "status", "format", "timezone", "speakers", "register_url",
                                         "calendar_url", "moodle_url", "recording_url")}
        raw["program_tags"] = f.get("program_tags", "").split(",")
        try:
            raw["sessions"] = parse_sessions(f.get("sessions", ""))
            doc = validate_doc(raw)
            if doc["status"] == "scheduled" and not doc.get("register_url"):
                raise ValueError("A scheduled (public) workshop needs a registration form link.")
            for k in ("zoom_link", "survey_url"):
                if f.get(k) and not f[k].strip().startswith("https://"):
                    raise ValueError("Links must start with https://.")
            if f.get("reg_close"):
                date.fromisoformat(f["reg_close"])
        except ValueError as exc:
            return RedirectResponse(f"/coord/w/{wid}?edit=1&" + urllib.parse.urlencode({"msg": str(exc)}) + "#details",
                                    status_code=303)
        live.save_workshop(doc, wid)
        coord.save_details(wid, {"zoom_link": f.get("zoom_link", "").strip(), "survey_url": f.get("survey_url", "").strip(),
                                 "reg_close": f.get("reg_close", ""), "notes": f.get("notes", "")[:4000],
                                 "prerequisites": _prereq_lines(f.get("prerequisites", ""))})
        return back(wid, msg="Saved.")

    @r.post("/coord/w/{wid}/delete", include_in_schema=False)
    async def delete(wid: int, request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        w = get_w(wid)
        if not w:
            return JSONResponse({"error": "not_found"}, 404)
        f = await form_of(request)
        if w.get("status") != "draft" or f.get("confirm") != "yes":
            return back(wid, "details", "Only drafts can be deleted, and the box must be ticked.")
        live.delete_workshop(wid)
        coord.forget_workshop(wid)
        return RedirectResponse("/coord?" + urllib.parse.urlencode({"msg": f"Deleted the draft “{w['title']}”."}),
                                status_code=303)

    @r.post("/coord/w/{wid}/step/{key}", include_in_schema=False)
    async def step(wid: int, key: str, request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        w = get_w(wid)
        if not w or key not in STEP_BY_KEY:
            return JSONResponse({"error": "not_found"}, 404)
        f = await form_of(request)
        a = f.get("action")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if a == "done":
            coord.save_task(wid, key, status="done", done_at=now, done_by=who(user))
        elif a == "undo":
            coord.save_task(wid, key, status="open", done_at=None, done_by=None)
        elif a == "na":
            coord.save_task(wid, key, status="na", done_at=now, done_by=who(user))
        elif a == "notes":
            coord.save_task(wid, key, notes=f.get("notes", "")[:2000])
        elif a == "due":
            try:
                coord.save_task(wid, key, due_date=date.fromisoformat(f.get("due", "")).isoformat())
            except ValueError:
                return back(wid, f"s-{key}", "That is not a date.")
        elif a == "field" and STEP_BY_KEY[key].get("field"):
            field, val = STEP_BY_KEY[key]["field"], f.get("value", "").strip()
            if val and not val.startswith("https://"):
                return back(wid, f"s-{key}", "Links must start with https://.")
            if field in ("zoom_link", "survey_url"):
                coord.save_details(wid, {field: val})
            else:
                live.save_workshop({field: val}, wid)
        else:
            return JSONResponse({"error": "bad_request"}, 400)
        return back(wid, f"s-{key}")

    @r.post("/coord/w/{wid}/copy/{channel}/{day}", include_in_schema=False)
    async def copy(wid: int, channel: str, day: int, request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        w = get_w(wid)
        if not w or channel not in CHANNEL_BY_KEY:
            return JSONResponse({"error": "not_found"}, 404)
        f = await form_of(request)
        a = f.get("action")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        share = CHANNEL_BY_KEY[channel].get("share")
        siblings = [c for c in CHANNEL_BY_KEY if share and CHANNEL_BY_KEY[c].get("share") == share] or [channel]
        anchor = f"c-{share}" if share and a in ("save", "reset", "to_offering") else f"c-{channel}-{day}"
        if a == "save":
            for c in siblings:  # newsletters share one body
                coord.save_comm(wid, c, day, edited_text=f.get("text", "")[:20000], subject=f.get("subject") or None)
        elif a == "reset":
            for c in siblings:
                coord.save_comm(wid, c, day, edited_text=None, subject=None)
        elif a == "sent":
            coord.save_comm(wid, channel, day, sent_at=now, sent_by=who(user))
        elif a == "unsent":
            coord.save_comm(wid, channel, day, sent_at=None, sent_by=None)
        elif a == "to_offering" and w.get("offering"):
            c = wf.copy(w, channel, day)
            ctx = build_context(w, coord.offering(w["offering"]), coord.details(wid), day)
            tok = tokenizer(ctx)
            coord.save_template(w["offering"], template_key(channel), tok(f.get("subject") or c["subject"]),
                                tok(f.get("text") or c["text"]))
            return back(wid, anchor, "Saved as this offering's wording; the next delivery starts from it.")
        else:
            return JSONResponse({"error": "bad_request"}, 400)
        return back(wid, anchor)

    @r.post("/coord/w/{wid}/outcome", include_in_schema=False)
    async def outcome(wid: int, request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        w = get_w(wid)
        if not w:
            return JSONResponse({"error": "not_found"}, 404)
        f = await form_of(request)
        doc = {k: _int(f.get(k)) for k in ("registered", "peak", "staff", "eval_responses")}
        if any(v is not None and v < 0 for v in doc.values()):
            return back(wid, "s-attendance", "Counts cannot be negative.")
        doc.update(eval_summary=f.get("eval_summary", "")[:4000], retro_notes=f.get("retro_notes", "")[:4000])
        coord.save_outcome(wid, doc)
        if f.get("to_offering") and w.get("offering") and doc["retro_notes"].strip():
            off = coord.offering(w["offering"])
            if off:
                stamp = f"[{date.today().isoformat()} · {w['title']}] {doc['retro_notes'].strip()}"
                coord.save_offering(off["slug"], {"retro_notes": ((off.get("retro_notes") or "") + "\n" + stamp).strip()})
        return back(wid, "s-attendance", "Closeout saved.")

    # ---------- offerings ----------
    @r.get("/coord/offerings", include_in_schema=False)
    def offerings(request: Request) -> Response:
        user, err = gate(request)
        if err:
            return err
        rows = "".join(
            f'<tr><td><a href="/coord/offerings/{E(o["slug"])}">{E(o["name"])}</a></td>'
            f'<td>{E(", ".join(o.get("program_tags") or []))}</td><td>{E(o.get("default_speakers") or "")}</td></tr>'
            for o in coord.offerings())
        body = ('<h1>Offerings</h1><p class="muted">Templates for workshops you run more than once: description, tags, '
                'speakers, prerequisites, and wording saved from past deliveries.</p><div class="card">'
                + (f'<table><thead><tr><th>Offering</th><th>Tags</th><th>Speakers</th></tr></thead><tbody>{rows}</tbody></table>'
                   if rows else '<p class="muted">No offerings yet.</p>')
                + '</div><h2>New offering</h2><div class="card"><form method="post" action="/coord/offerings"><div class="grid">'
                '<div><label for="o-slug">Short id (letters, numbers, dashes)</label><input id="o-slug" name="slug" required pattern="[a-z0-9][a-z0-9-]{1,60}"></div>'
                '<div><label for="o-name">Name</label><input id="o-name" name="name" required></div></div>'
                '<div class="row"><button class="primary">Create</button></div></form></div>')
        return page(user, "Offerings", body)

    @r.get("/coord/offerings/{slug}", include_in_schema=False)
    def offering(slug: str, request: Request) -> Response:
        user, err = gate(request)
        if err:
            return err
        o = coord.offering(slug)
        if not o:
            return HTMLResponse("<p>No such offering.</p>", status_code=404)
        deliveries = sorted([w for w in live.list_workshops() if w.get("offering") == slug and first_day(w)],
                            key=first_day, reverse=True)
        saved = [k for k in ("newsletters", "access_calendar", "delta_users", "icc_users", "partner_lists",
                             "webtools_calendar", "reminder_email", "eval_invite") if coord.template(slug, k)]
        msg = request.query_params.get("msg")
        body = (
            f'<h1>{E(o["name"])}</h1>' + (f'<p class="note" role="status">{E(msg)}</p>' if msg else "")
            + f'<form method="post" action="/coord/offerings/{E(slug)}" class="card">'
            f'<label for="f-name">Name</label><input id="f-name" name="name" value="{E(o["name"])}" required>'
            f'<label for="f-desc">Description</label><textarea id="f-desc" name="description">{E(o.get("description") or "")}</textarea>'
            '<div class="grid">'
            f'<div><label for="f-tags">Program tags</label><input id="f-tags" name="program_tags" value="{E(", ".join(o.get("program_tags") or []))}"></div>'
            f'<div><label for="f-sp">Default speakers</label><input id="f-sp" name="default_speakers" value="{E(o.get("default_speakers") or "")}"></div>'
            f'<div><label for="f-aud">Audience</label><input id="f-aud" name="target_audience" value="{E(o.get("target_audience") or "")}"></div>'
            f'<div><label for="f-min">Typical minutes</label><input id="f-min" name="typical_minutes" inputmode="numeric" value="{E(str(o.get("typical_minutes") or ""))}"></div>'
            f'<div><label for="f-mdl">Learn@NCSA course</label><input id="f-mdl" name="moodle_url" value="{E(o.get("moodle_url") or "")}"></div>'
            f'<div><label for="f-form">Last Webtools form id</label><input id="f-form" name="prior_form_id" value="{E(o.get("prior_form_id") or "")}"></div></div>'
            f'<label for="f-pre">Prerequisites, one per line: label | link</label><textarea id="f-pre" name="prerequisites" style="min-height:60px">{E(_prereq_text(o.get("prerequisites") or []))}</textarea>'
            f'<label for="f-retro">What to change next time (from closeouts)</label><textarea id="f-retro" name="retro_notes">{E(o.get("retro_notes") or "")}</textarea>'
            '<label class="small" style="font-weight:400"><input type="checkbox" name="archived" value="1" style="width:auto"'
            + (" checked" if o.get("archived") else "") + '> archived (hidden from "Plan a workshop")</label>'
            '<div class="row"><button class="primary">Save offering</button></div></form>'
            f'<h2>Saved wording</h2><div class="card">{E(", ".join(saved)) if saved else "<span class=muted>None yet — the defaults and last delivery are used.</span>"}</div>'
            '<h2>Deliveries</h2><div class="card">'
            + ("".join(f'<p><a href="/coord/w/{w["id"]}">{E(first_day(w) or "")} · {E(w["title"])}</a> '
                       f'<span class="chip">{E(w.get("status") or "")}</span></p>' for w in deliveries)
               or '<p class="muted">None yet.</p>') + '</div>')
        return page(user, o["name"], body)

    @r.post("/coord/offerings", include_in_schema=False)
    async def new_offering(request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        f = await form_of(request)
        slug = f.get("slug", "").strip().lower()
        if not SLUG.match(slug) or coord.offering(slug) or not f.get("name", "").strip():
            return RedirectResponse("/coord/offerings", status_code=303)
        coord.save_offering(slug, {"name": f["name"].strip()[:200]})
        return RedirectResponse(f"/coord/offerings/{slug}", status_code=303)

    @r.post("/coord/offerings/{slug}", include_in_schema=False)
    async def save_offering(slug: str, request: Request) -> Response:
        user, err = gate(request, write=True)
        if err:
            return err
        if not coord.offering(slug):
            return JSONResponse({"error": "not_found"}, 404)
        f = await form_of(request)
        url = f.get("moodle_url", "").strip()
        if url and not url.startswith("https://"):
            return RedirectResponse(f"/coord/offerings/{slug}?" + urllib.parse.urlencode({"msg": "Links must start with https://."}),
                                    status_code=303)
        coord.save_offering(slug, {
            "name": f.get("name", "").strip()[:200] or slug, "description": f.get("description", "")[:4000],
            "program_tags": [t.strip()[:30] for t in f.get("program_tags", "").split(",") if t.strip()][:12],
            "default_speakers": f.get("default_speakers", "")[:300], "target_audience": f.get("target_audience", "")[:300],
            "typical_minutes": _int(f.get("typical_minutes")) or 90, "moodle_url": url,
            "prior_form_id": f.get("prior_form_id", "")[:60], "prerequisites": _prereq_lines(f.get("prerequisites", "")),
            "retro_notes": f.get("retro_notes", "")[:8000], "archived": 1 if f.get("archived") else 0})
        return RedirectResponse(f"/coord/offerings/{slug}?msg=Saved.", status_code=303)

    # ---------- v1 import (admin) ----------
    @r.post("/api/v1/admin/coord/import")
    async def import_v1(request: Request) -> Response:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required"}, 401)
        if not is_admin(user):
            return JSONResponse({"error": "forbidden"}, 403)
        if not _same_origin(request, get_base_url()):
            return JSONResponse({"error": "forbidden"}, 403)
        try:
            data = json.loads(await request.body())
        except ValueError:
            return JSONResponse({"error": "bad_request"}, 400)
        n_off = n_ev = 0
        for o in data.get("offerings", []):
            if SLUG.match(str(o.get("slug", ""))):
                coord.save_offering(o["slug"], {k: o[k] for k in CoordStore.OFFERING_FIELDS if k in o})
                n_off += 1
        by_key = {w.get("source_key"): w for w in live.list_workshops() if w.get("source_key")}
        missing = []
        for ev in data.get("events", []):
            w = by_key.get(ev.get("source_key"))
            if not w:
                missing.append(ev.get("source_key"))
                continue
            for t in ev.get("tasks", []):
                if t.get("step_key") in STEP_BY_KEY:
                    coord.save_task(w["id"], t["step_key"], status=t.get("status", "open"), notes=t.get("notes", ""),
                                    done_at=t.get("done_at"), done_by=t.get("done_by"))
            for c in ev.get("comms", []):
                if c.get("channel") in CHANNEL_BY_KEY:
                    coord.save_comm(w["id"], c["channel"], int(c.get("day_index") or 0),
                                    edited_text=c.get("edited_text"), sent_at=c.get("sent_at"), subject=c.get("subject"))
            if ev.get("details"):
                coord.save_details(w["id"], ev["details"])
            if ev.get("outcome"):
                coord.save_outcome(w["id"], ev["outcome"])
            n_ev += 1
        return JSONResponse({"offerings": n_off, "events": n_ev, "missing": missing})

    return r
