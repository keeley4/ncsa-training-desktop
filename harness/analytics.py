"""Usage analytics (Phase 5 prototype): aggregate-only, first-party, admin-only reports.

Collected: no user ids and no IP addresses. Each browser session gets a random id (kept in sessionStorage)
so visits can be counted; events say only whether the visitor was signed in.

  POST /api/v1/usage                 beacon from the front door: {"sid": "...", "events": [{kind, target, detail, n}]}
  GET  /api/v1/admin/analytics?days= JSON summary (admins only)
  GET  /admin/analytics?days=        HTML dashboard (admins only)

Kept 395 days, then deleted.
"""
from __future__ import annotations

import html
import json
import random
import re
import threading
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .admin import _same_origin
from . import feed
from .live_store import retention_cutoff

KINDS = {
    "visit": "Visits", "search": "Searches", "filter": "Filter used", "tile_open": "Course details opened",
    "launch": "Launches to Learn@NCSA", "continue": "Continue clicks", "workshops_view": "Workshops page views",
    "register_click": "Register clicks", "ics_download": "Calendar downloads", "signin": "Sign-ins",
}
CLIENT_KINDS = set(KINDS) - {"signin"}
_SID = re.compile(r"^[a-f0-9]{16,32}$")
MAX_EVENTS = 25
PER_SID_HOUR = 400


def _clip(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def clean_events(body: Any, signed_in: bool) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not _SID.match(str(body.get("sid", ""))):
        raise ValueError("bad sid")
    events = body.get("events")
    if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
        raise ValueError("bad events")
    out = []
    for e in events:
        if not isinstance(e, dict) or e.get("kind") not in CLIENT_KINDS:
            continue
        n = e.get("n")
        out.append({"sid": body["sid"], "signed_in": signed_in, "kind": e["kind"],
                    "target": _clip(e.get("target"), 80),
                    "detail": _clip(e.get("detail"), 80).lower() if e["kind"] == "search" else _clip(e.get("detail"), 40),
                    "n": int(n) if isinstance(n, (int, float)) and 0 <= n < 1_000_000 else None})
    return out


def summarize(rows: list[dict[str, Any]], days: int, now: datetime, extras: dict[str, Any]) -> dict[str, Any]:
    by_kind = Counter(r["kind"] for r in rows)
    sessions = {r["sid"] for r in rows if r["kind"] == "visit" and r["sid"]}
    signed_sessions = {r["sid"] for r in rows if r["kind"] == "visit" and r["signed_in"] and r["sid"]}
    daily: dict[str, set] = defaultdict(set)
    for r in rows:
        if r["kind"] == "visit":
            daily[r["at"].astimezone(timezone.utc).date().isoformat()].add(r["sid"])
    start = (now - timedelta(days=days - 1)).date()
    series = [{"date": (start + timedelta(days=i)).isoformat(),
               "visits": len(daily.get((start + timedelta(days=i)).isoformat(), ()))} for i in range(days)]
    searches = Counter(r["detail"] for r in rows if r["kind"] == "search" and r["detail"])
    zero = Counter(r["detail"] for r in rows if r["kind"] == "search" and r["detail"] and r["n"] == 0)
    opens = Counter(r["target"] for r in rows if r["kind"] == "tile_open")
    launches = Counter(r["target"] for r in rows if r["kind"] == "launch")
    tiles = sorted(set(opens) | set(launches), key=lambda t: (-opens[t], -launches[t]))
    wk = defaultdict(Counter)
    for r in rows:
        if r["kind"] in ("register_click", "ics_download") and r["target"]:
            wk[r["target"]][r["kind"]] += 1
    return {
        "days": days,
        "totals": {"sessions": len(sessions), "signedInSessions": len(signed_sessions),
                   **{k: by_kind.get(k, 0) for k in KINDS if k != "visit"}},
        "daily": series,
        "topSearches": [{"term": t, "count": c, "zeroResults": zero.get(t, 0)} for t, c in searches.most_common(15)],
        "zeroResultSearches": [{"term": t, "count": c} for t, c in zero.most_common(10)],
        "tiles": [{"tileId": t, "opens": opens[t], "launches": launches[t]} for t in tiles[:20]],
        "filters": dict(Counter(r["target"] for r in rows if r["kind"] == "filter")),
        "workshopActions": {k: dict(v) for k, v in wk.items()},
        **extras,
    }


def make_analytics_router(store: Any, auth_store: Any, enrol_log_rows: Callable[[int], list[dict[str, Any]]],
                          catalog_titles: Callable[[], dict[str, Any]], current_user: Callable[[Request], Any],
                          is_admin: Callable[[Any], bool], get_base_url: Callable[[], str | None]) -> APIRouter:
    r = APIRouter()
    seen: dict[str, list[float]] = {}
    lock = threading.Lock()

    @r.post("/api/v1/usage", include_in_schema=False)
    async def usage(request: Request) -> Response:
        if not _same_origin(request, get_base_url()):
            return Response(status_code=204)  # silently ignore cross-site beacons
        try:
            raw = await request.body()
            if len(raw) > 16_000:
                raise ValueError("too big")
            rows = clean_events(json.loads(raw), current_user(request) is not None)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad_request"}, 400)
        now = time.time()
        with lock:
            hits = [t for t in seen.get(rows[0]["sid"] if rows else "", []) if t > now - 3600]
            if rows and len(hits) + len(rows) > PER_SID_HOUR:
                return Response(status_code=204)
            if rows:
                seen[rows[0]["sid"]] = hits + [now] * len(rows)
            if len(seen) > 5000:
                seen.clear()
        store.add_usage(rows)
        if random.random() < 0.01:
            store.purge_usage(retention_cutoff())
        return Response(status_code=204)

    def report(days: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        rows = store.usage_since(now - timedelta(days=days))
        titles = catalog_titles()
        links = auth_store.link_counts() if hasattr(auth_store, "link_counts") else {}
        enrol = enrol_log_rows(days)
        by_course: dict[int, Counter] = defaultdict(Counter)
        for e in enrol:
            by_course[int(e["course_id"])][e["result"].split(":")[0]] += 1
        stats = store.course_stats()
        courses = []
        for cid, t in sorted(titles["courses"].items(), key=lambda kv: kv[1]):
            courses.append({"courseId": cid, "title": t, "enrolled": (stats.get(cid) or {}).get("enrolled"),
                            "oneClick": dict(by_course.get(cid, {}))})
        checked = max((s["checkedAt"] for s in stats.values()), default=None)
        workshops = {str(w["id"]): w["title"] for w in store.list_workshops()}
        out = summarize(rows, days, now, {
            "accounts": links,
            "courses": courses, "courseStatsCheckedAt": checked,
            "oneClickTotals": dict(sum((Counter(v) for v in by_course.values()), Counter())),
        })
        out["tileTitles"] = {t["tileId"]: titles["tiles"].get(t["tileId"], t["tileId"]) for t in out["tiles"]}
        out["workshopTitles"] = {k: workshops.get(k, f"Workshop {k}") for k in out["workshopActions"]}
        out["learn"] = feed.report(store, days, titles["courses"], now)
        out["followThrough"] = feed.follow_through(store, store.list_workshops(), now=now) if out["learn"]["available"] else []
        return out

    def guard(request: Request) -> Response | None:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required"}, 401)
        if not is_admin(user):
            return JSONResponse({"error": "forbidden"}, 403)
        return None

    def days_of(v: str | None) -> int:
        return int(v) if v in ("7", "30", "90", "365") else 30

    @r.get("/api/v1/admin/analytics")
    def api_report(request: Request, days: str | None = None) -> Response:
        err = guard(request)
        if err:
            return err
        return JSONResponse(report(days_of(days)), headers={"Cache-Control": "no-store"})

    @r.get("/admin/analytics", include_in_schema=False)
    def dashboard(request: Request, days: str | None = None) -> Response:
        err = guard(request)
        if err:
            if err.status_code == 401:
                return RedirectResponse("/auth/login?next=/admin/analytics", status_code=303)
            return HTMLResponse("<p>Administrators only.</p>", status_code=403)
        return HTMLResponse(render(report(days_of(days))), headers={"Cache-Control": "no-store"})

    return r


# ---------------------------------------------------------------- HTML

def _bars(series: list[dict[str, Any]]) -> str:
    w, h, pad = 720, 140, 24
    peak = max([d["visits"] for d in series] + [1])
    bw = (w - pad) / max(len(series), 1)
    rects = []
    for i, d in enumerate(series):
        bh = (h - 20) * d["visits"] / peak
        x = pad + i * bw
        rects.append(f'<rect x="{x + 1:.1f}" y="{h - bh:.1f}" width="{max(bw - 2, 1):.1f}" height="{bh:.1f}" rx="2">'
                     f'<title>{d["date"]}: {d["visits"]} visits</title></rect>')
    first, last = series[0]["date"] if series else "", series[-1]["date"] if series else ""
    return (f'<svg viewBox="0 0 {w} {h + 18}" role="img" aria-label="Visits per day, {first} to {last}, peak {peak}" '
            f'class="chart"><text x="0" y="12" class="axis">{peak}</text><text x="0" y="{h}" class="axis">0</text>'
            f'<g class="bars">{"".join(rects)}</g>'
            f'<text x="{pad}" y="{h + 16}" class="axis">{first}</text>'
            f'<text x="{w}" y="{h + 16}" class="axis" text-anchor="end">{last}</text></svg>')


def _table(head: list[str], rows: list[list[Any]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{html.escape(empty)}</p>'
    th = "".join(f"<th{' class=num' if i else ''}>{html.escape(h)}</th>" for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td{' class=num' if i else ''}>{html.escape(str(c))}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _learn(d: dict[str, Any]) -> str:
    lr = d.get("learn") or {}
    if not lr.get("available"):
        return ('<h2>Learn@NCSA activity</h2><div class="card"><p class="muted">No activity feed yet (needs the '
                'local_ncsahub plugin on Learn@NCSA; pulled hourly).</p></div>')
    t = lr["totals"]
    rows = [[c["title"], c["newEnrolments"], c["completions"], c["activityCompletions"], c["completionsAllTime"]]
            for c in lr["courses"]]
    ft = [[f'{x["date"]} · {x["title"]}', f'{x["enrolBefore"]} → {x["enrolAfter"]}',
           f'{x["completeBefore"]} → {x["completeAfter"]}', "in progress" if x["partial"] else ""]
          for x in d.get("followThrough") or []]
    return (
        '<h2>Learn@NCSA activity</h2><div class="kpis">'
        + "".join(f'<div class="kpi"><div class="v">{v:,}</div><div class="l">{html.escape(l)}</div></div>' for l, v in [
            ("New enrolments", t["newEnrolments"]), ("Course completions", t["courseCompletions"]),
            ("Activities completed", t["activityCompletions"]), ("Active learners", t["activeLearners"]),
            ("Course completions, all time", t["completionsAllTime"])])
        + '</div><div class="card" style="margin-top:10px">'
        + _table(["Course", "New enrolments", "Completions", "Activities completed", "Completions (all time)"], rows,
                 "No course activity in this period.")
        + f'<p class="muted">Feed since {html.escape(lr["since"][:10])}. Counts are learners, not names.</p></div>'
        '<h2>Workshop follow-through</h2><div class="card">'
        + _table(["Workshop (linked course)", "Enrolments: 30 days before → after", "Completions: before → after", ""], ft,
                 "No delivered workshops linked to a Learn@NCSA course yet.")
        + '</div>')


def render(d: dict[str, Any]) -> str:
    t = d["totals"]
    kpi = lambda label, v, sub="": (f'<div class="kpi"><div class="v">{v:,}</div><div class="l">{html.escape(label)}</div>'  # noqa: E731
                                    f'{f"<div class=s>{html.escape(sub)}</div>" if sub else ""}</div>')
    pct = f"{round(100 * t['signedInSessions'] / t['sessions'])}% signed in" if t["sessions"] else ""
    tabs = "".join(f'<a href="?days={n}"{" aria-current=page" if d["days"] == n else ""}>{n} days</a>' for n in (7, 30, 90, 365))
    acc = d.get("accounts") or {}
    one = d.get("oneClickTotals") or {}
    courses = [[c["title"], "—" if c["enrolled"] is None else c["enrolled"], c["oneClick"].get("enrolled", 0)]
               for c in d["courses"] if c["enrolled"] or c["oneClick"]]
    tiles = [[d["tileTitles"].get(x["tileId"], x["tileId"]), x["opens"], x["launches"]] for x in d["tiles"]]
    wks = [[d["workshopTitles"].get(k, k), v.get("register_click", 0), v.get("ics_download", 0)]
           for k, v in d["workshopActions"].items()]
    checked = d.get("courseStatsCheckedAt")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex"><title>Usage analytics</title><style>'
        ':root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5b6573;--line:#dde1e6;--bar:#c8442b}'
        '@media(prefers-color-scheme:dark){:root{--bg:#11151c;--card:#1a202a;--text:#e8ecf2;--muted:#a9b3c1;--line:#2f3746;--bar:#f08a6e}}'
        'body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text);max-width:1000px;margin:0 auto;padding:20px 16px}'
        'a{color:inherit}h1{margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}.muted{color:var(--muted)}'
        '.tabs a{margin-right:12px}.tabs a[aria-current]{font-weight:700}'
        '.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:16px}'
        '.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.kpi .v{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}'
        '.kpi .l{font-size:13px}.kpi .s{font-size:12px;color:var(--muted)}'
        '.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;overflow-x:auto}'
        '.chart{width:100%;height:auto}.bars rect{fill:var(--bar)}.axis{font-size:11px;fill:var(--muted)}'
        'table{border-collapse:collapse;width:100%}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left}'
        '.num{text-align:right;font-variant-numeric:tabular-nums}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}'
        '@media(max-width:700px){.grid{grid-template-columns:1fr}}</style></head><body>'
        '<h1>Usage analytics</h1><p class="muted">Aggregate only: no user ids or IP addresses are stored. Prototype.</p>'
        f'<nav class="tabs" aria-label="Period">{tabs}</nav>'
        '<div class="kpis">'
        + kpi("Visits (browser sessions)", t["sessions"], pct)
        + kpi("Searches", t["search"]) + kpi("Course details opened", t["tile_open"])
        + kpi("Launches to Learn@NCSA", t["launch"]) + kpi("Sign-ins", t["signin"])
        + kpi("One-click enrollments", one.get("enrolled", 0))
        + kpi("Workshop register clicks", t["register_click"]) + kpi("Calendar downloads", t["ics_download"])
        + '</div>'
        f'<h2>Visits per day</h2><div class="card">{_bars(d["daily"])}</div>'
        '<div class="grid"><div>'
        '<h2>Top searches</h2><div class="card">'
        + _table(["Term", "Count", "No results"], [[s["term"], s["count"], s["zeroResults"]] for s in d["topSearches"]],
                 "No searches yet.")
        + '</div></div><div><h2>Courses people open</h2><div class="card">'
        + _table(["Course or program", "Opened", "Launched"], tiles, "No course details opened yet.")
        + '</div></div></div>'
        '<h2>Searches that found nothing</h2><div class="card">'
        + _table(["Term", "Count"], [[s["term"], s["count"]] for s in d["zeroResultSearches"]], "None — every search found something.")
        + '</div><div class="grid"><div><h2>Accounts</h2><div class="card">'
        + _table(["Learn@NCSA link", "People"], [[k, v] for k, v in sorted(acc.items())], "No sign-ins yet.")
        + '</div></div><div><h2>Workshops</h2><div class="card">'
        + _table(["Workshop", "Register clicks", "Calendar downloads"], wks, "No workshop activity yet.")
        + '</div></div></div>'
        '<h2>Learn@NCSA enrollment by course</h2><div class="card">'
        + _table(["Course", "Enrolled now", "One-click enrollments (period)"], courses,
                 "No enrollment counts yet (refreshed hourly once the course-stats step is set up).")
        + (f'<p class="muted">Enrollment counts as of {html.escape(checked[:16].replace("T", " "))} UTC.'
           ' Completion counts need the Learn@NCSA event stream (later phase).</p>' if checked else
           '<p class="muted">Completion counts need the Learn@NCSA event stream (later phase).</p>')
        + '</div>' + _learn(d) + '<p class="muted"><a href="/api/v1/admin/analytics?days=' + str(d["days"]) + '">JSON</a> · '
        '<a href="/admin/workshops">Workshops</a> · <a href="/admin/review">Account review</a> · <a href="/">Front door</a></p>'
        '</body></html>')
