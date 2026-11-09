"""Learn@NCSA activity feed (Phase 5): pull the local_ncsahub outbox and report on it.

The Moodle plugin records enrolments, unenrolments, course completions and activity completions (Moodle user
id, course, time; no names or emails) plus a one-time backfill of past course completions. The harness pulls
new rows hourly with `python -m harness pull-feed` (afterid = highest id already stored) and keeps them in
`moodle_events`. Reports are aggregate counts on the admin analytics page.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

KINDS = ("enrolled", "unenrolled", "course_completed", "activity_completion")
_COURSE = re.compile(r"/course/view\.php\?id=(\d+)")


def pull(store: Any, call: Callable[..., Any], page_size: int = 2000, max_pages: int = 50) -> int:
    """Fetch everything after the last stored id. Returns the number of new events stored."""
    after, total = store.feed_last_id(), 0
    for _ in range(max_pages):
        res = call("local_ncsahub_get_events", afterid=after, limit=page_size) or {}
        rows = [{"id": int(e["id"]), "kind": e["kind"], "moodle_user": int(e["userid"]), "course_id": int(e["courseid"]),
                 "cm_id": int(e.get("cmid") or 0), "state": int(e.get("state") or 0),
                 "at": datetime.fromtimestamp(int(e["timecreated"]), timezone.utc)}
                for e in res.get("events", []) if e.get("kind") in KINDS]
        total += store.add_feed_events(rows)
        after = int(res.get("lastid") or after)
        if not res.get("more"):
            break
    return total


def course_of(url: str | None) -> int | None:
    m = _COURSE.search(url or "")
    return int(m.group(1)) if m else None


def report(store: Any, days: int, course_titles: dict[int, str], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    all_rows = store.feed_events(KINDS)
    if not all_rows:
        return {"available": False}
    period = [r for r in all_rows if r["at"] >= since]
    completed_all: dict[int, set] = defaultdict(set)
    for r in all_rows:
        if r["kind"] == "course_completed":
            completed_all[r["course_id"]].add(r["moodle_user"])
    per = defaultdict(lambda: {"enrolled": 0, "completed": set(), "activities": 0, "active": set()})
    for r in period:
        p = per[r["course_id"]]
        if r["kind"] == "enrolled":
            p["enrolled"] += 1
        elif r["kind"] == "course_completed":
            p["completed"].add(r["moodle_user"])
        elif r["kind"] == "activity_completion" and r["state"] in (1, 2):
            p["activities"] += 1
        if r["kind"] != "unenrolled":
            p["active"].add(r["moodle_user"])
    courses = []
    for cid in sorted(set(per) | set(completed_all), key=lambda c: (-len(per[c]["completed"]) if c in per else 0,
                                                                     -len(completed_all.get(c, ())))):
        p = per.get(cid) or {"enrolled": 0, "completed": set(), "activities": 0, "active": set()}
        courses.append({"courseId": cid, "title": course_titles.get(cid, f"Course {cid}"), "newEnrolments": p["enrolled"],
                        "completions": len(p["completed"]), "activityCompletions": p["activities"],
                        "activeLearners": len(p["active"]), "completionsAllTime": len(completed_all.get(cid, ()))})
    active = {r["moodle_user"] for r in period if r["kind"] != "unenrolled"}
    return {
        "available": True,
        "totals": {"newEnrolments": sum(1 for r in period if r["kind"] == "enrolled"),
                   "courseCompletions": sum(1 for r in period if r["kind"] == "course_completed"),
                   "activityCompletions": sum(1 for r in period if r["kind"] == "activity_completion" and r["state"] in (1, 2)),
                   "activeLearners": len(active),
                   "completionsAllTime": sum(len(v) for v in completed_all.values())},
        "courses": courses[:40],
        "since": min(r["at"] for r in all_rows).isoformat(),
    }


def follow_through(store: Any, workshops: list[dict[str, Any]], window: int = 30,
                   now: datetime | None = None) -> list[dict[str, Any]]:
    """For delivered workshops linked to a Learn@NCSA course: enrolments and completions in that course in the
    `window` days after the first session, against the same window before it. Aggregate counts only."""
    now = now or datetime.now(timezone.utc)
    rows = store.feed_events(("enrolled", "course_completed"))
    by_course: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_course[r["course_id"]].append(r)
    out = []
    for w in workshops:
        cid = course_of(w.get("moodle_url"))
        s = (w.get("sessions") or [{}])[0].get("start")
        if not cid or not s or w.get("status") == "cancelled":
            continue
        start = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        if start > now:
            continue
        before = [r for r in by_course[cid] if start - timedelta(days=window) <= r["at"] < start]
        after = [r for r in by_course[cid] if start <= r["at"] < start + timedelta(days=window)]
        out.append({"workshopId": w["id"], "title": w["title"], "date": s[:10], "courseId": cid,
                    "enrolBefore": sum(1 for r in before if r["kind"] == "enrolled"),
                    "enrolAfter": sum(1 for r in after if r["kind"] == "enrolled"),
                    "completeBefore": sum(1 for r in before if r["kind"] == "course_completed"),
                    "completeAfter": sum(1 for r in after if r["kind"] == "course_completed"),
                    "partial": start + timedelta(days=window) > now})
    return sorted(out, key=lambda x: x["date"], reverse=True)
