"""Learner features (Phase 3): My Desktop progress ("Continue") and enroll-on-launch.

  GET  /api/v1/me/progress   the signed-in learner's Learn@NCSA courses, progress and last access
  POST /api/v1/enrollments   {"courseId": N}  enroll the signed-in learner as a student, then open the course

Both use the IP-restricted learner-service token (MOODLE_LOOKUP_TOKEN_FILE) and act only on the Moodle
account linked to the person who is signed in (identity crosswalk). Nothing is stored about courses or
progress; enrolment attempts are logged (who, which course, result) for rate limits and audit.

Enrollment rules (all must hold), otherwise the course simply opens in Learn@NCSA as before:
  - ENROL_ON_LAUNCH=1 on the host (off switch);
  - the person is signed in and their Learn@NCSA account is linked;
  - the course is on the published front door (catalog), and Moodle reports an enabled self-enrolment
    method that is open now with no enrolment key (so anyone could already enrol themselves);
  - rate limits: per person ENROL_LIMIT_HOUR (5) per hour and ENROL_LIMIT_DAY (20) per day,
    site-wide ENROL_LIMIT_SITE_DAY (300) per day.
The role is always student (Moodle role id ENROL_ROLE_ID, default 5).
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .admin import _same_origin
from .moodle import MoodleError

CallFn = Callable[..., Any]          # call(wsfunction, **params) -> decoded JSON
_COURSE_URL = re.compile(r"/course/view\.php\?id=(\d+)")
CACHE_SECONDS = 60


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def catalog_courses(store: Any) -> dict[int, dict[str, Any]]:
    """Course id -> {"tileId", "title"} for every course on the published front door."""
    out: dict[int, dict[str, Any]] = {}
    for t in store.tiles().get("items", []):
        if t.get("type") == "tool":
            continue
        urls = [(t.get("launch") or {}).get("url", "")] + [c.get("url", "") for c in t.get("children") or []]
        for i, url in enumerate(urls):
            m = _COURSE_URL.search(url or "")
            if m:
                cid = int(m.group(1))
                title = t["title"] if i == 0 else (t.get("children") or [])[i - 1].get("title") or t["title"]
                out.setdefault(cid, {"tileId": t["id"], "title": title})
    return out


def _open_self_enrolment(methods: list[dict[str, Any]]) -> bool:
    for m in methods or []:
        status = m.get("status")
        ok = status is True or str(status).lower() in {"1", "true"}
        if m.get("type") == "self" and ok and not m.get("wsfunction"):
            return True
    return False


def _iso(ts: Any) -> str | None:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts > 0 else None


class MemoryEnrolLog:
    def __init__(self) -> None:
        self.rows: list[tuple[int | None, int, int, str, float]] = []

    def log_enrolment(self, user_id, moodle_id, course_id, result):
        self.rows.append((user_id, moodle_id, course_id, result, time.time()))

    def rows_since(self, days):
        cutoff = time.time() - days * 86400
        return [{"course_id": r[2], "result": r[3]} for r in self.rows if r[4] >= cutoff]

    def count_enrolments(self, user_id, seconds):
        cutoff = time.time() - seconds
        return sum(1 for r in self.rows if r[3] == "enrolled" and r[4] >= cutoff and (user_id is None or r[0] == user_id))


class PgEnrolLog:
    def __init__(self, dsn: str):
        import psycopg

        self._psycopg = psycopg
        self.dsn = dsn

    def log_enrolment(self, user_id, moodle_id, course_id, result):
        with self._psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO enrol_events (user_id, moodle_id, course_id, result) VALUES (%s, %s, %s, %s)",
                         (user_id, moodle_id, course_id, result))
            conn.commit()

    def rows_since(self, days):
        with self._psycopg.connect(self.dsn) as conn:
            rows = conn.execute("SELECT course_id, result FROM enrol_events "
                                "WHERE created_at > now() - make_interval(days => %s)", (days,)).fetchall()
        return [{"course_id": r[0], "result": r[1]} for r in rows]

    def count_enrolments(self, user_id, seconds):
        with self._psycopg.connect(self.dsn) as conn:
            if user_id is None:
                row = conn.execute("SELECT count(*) FROM enrol_events WHERE result='enrolled' "
                                   "AND created_at > now() - make_interval(secs => %s)", (seconds,)).fetchone()
            else:
                row = conn.execute("SELECT count(*) FROM enrol_events WHERE result='enrolled' AND user_id=%s "
                                   "AND created_at > now() - make_interval(secs => %s)", (user_id, seconds)).fetchone()
        return int(row[0])


def make_learner_router(get_call: Callable[[], CallFn | None], catalog_store: Callable[[], Any],
                        current_user: Callable[[Request], dict[str, Any] | None], log: Any,
                        get_base_url: Callable[[], str | None], get_moodle_url: Callable[[], str | None]) -> APIRouter:
    r = APIRouter()
    cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
    lock = threading.Lock()

    def site() -> str:
        return (get_moodle_url() or "").rstrip("/")

    def course_url(cid: int) -> str:
        return f"{site()}/course/view.php?id={cid}"

    def moodle_courses(call: CallFn, moodle_id: int, fresh: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        with lock:
            hit = cache.get(moodle_id)
        if hit and not fresh and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        rows = call("core_enrol_get_users_courses", userid=moodle_id, returnusercount=0) or []
        with lock:
            cache[moodle_id] = (now, rows)
        return rows

    def linked(user: dict[str, Any]) -> int | None:
        link = user.get("moodle") or {}
        return int(link["moodleId"]) if link.get("status") == "linked" and link.get("moodleId") else None

    @r.get("/api/v1/me/progress")
    def progress(request: Request) -> Response:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        headers = {"Cache-Control": "no-store"}
        mid = linked(user)
        status = (user.get("moodle") or {}).get("status", "unavailable")
        call = get_call()
        if mid is None or call is None:
            return JSONResponse({"status": status if mid is None else "unavailable", "items": []}, headers=headers)
        try:
            rows = moodle_courses(call, mid)
        except MoodleError:
            return JSONResponse({"status": "unavailable", "items": []}, headers=headers)
        cat = catalog_courses(catalog_store())
        items = []
        for c in rows:
            if not c.get("visible", 1):
                continue
            cid = int(c["id"])
            prog = c.get("progress")
            items.append({
                "courseId": cid,
                "title": c.get("fullname") or c.get("displayname") or f"Course {cid}",
                "url": course_url(cid),
                "tileId": (cat.get(cid) or {}).get("tileId"),
                "progress": round(float(prog)) if prog is not None else None,
                "completed": bool(c.get("completed")) if c.get("completed") is not None else None,
                "lastAccess": _iso(c.get("lastaccess")),
            })
        items.sort(key=lambda i: i["lastAccess"] or "", reverse=True)
        return JSONResponse({"status": "linked", "items": items}, headers=headers)

    @r.post("/api/v1/enrollments")
    async def enrol(request: Request) -> Response:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        if not _same_origin(request, get_base_url()):
            return JSONResponse({"error": "forbidden", "message": "Cross-site request refused."}, 403)
        try:
            body = await request.json()
            cid = int(body.get("courseId"))
        except Exception:
            return JSONResponse({"error": "bad_request", "message": "Send {\"courseId\": <number>}."}, 400)
        url = course_url(cid)

        def no(code: str, message: str, status: int = 409) -> Response:
            return JSONResponse({"enrolled": False, "error": code, "message": message, "url": url}, status)

        if cid not in catalog_courses(catalog_store()):
            return no("not_in_catalog", "That course is not on the front door.", 404)
        if os.environ.get("ENROL_ON_LAUNCH", "0") != "1":
            return no("enrol_off", "Enroll-on-launch is turned off; enroll in Learn@NCSA.")
        mid, call = linked(user), get_call()
        if mid is None or call is None:
            return no("not_linked", "Your Learn@NCSA account is not linked yet; enroll in Learn@NCSA.")
        uid = user.get("id")
        try:
            if any(int(c["id"]) == cid for c in moodle_courses(call, mid, fresh=True)):
                return JSONResponse({"enrolled": True, "already": True, "url": url})
            if not _open_self_enrolment(call("core_enrol_get_course_enrolment_methods", courseid=cid)):
                return no("not_open", "This course does not allow self-enrollment; Learn@NCSA explains how to join.")
        except MoodleError:
            return no("moodle_unavailable", "Learn@NCSA could not be reached; try opening the course directly.", 502)
        limits = [(uid, 3600, _int_env("ENROL_LIMIT_HOUR", 5)), (uid, 86400, _int_env("ENROL_LIMIT_DAY", 20)),
                  (None, 86400, _int_env("ENROL_LIMIT_SITE_DAY", 300))]
        for who, secs, limit in limits:
            if log.count_enrolments(who, secs) >= limit:
                log.log_enrolment(uid, mid, cid, "rate_limited")
                return no("rate_limited", "Too many enrollments right now; enroll in Learn@NCSA or try later.", 429)
        try:
            call("enrol_manual_enrol_users",
                 enrolments=[{"roleid": _int_env("ENROL_ROLE_ID", 5), "userid": mid, "courseid": cid}])
        except MoodleError as exc:
            log.log_enrolment(uid, mid, cid, f"error:{exc.errorcode}"[:60])
            return no("moodle_refused", "Learn@NCSA did not accept the enrollment; enroll there instead.", 502)
        log.log_enrolment(uid, mid, cid, "enrolled")
        with lock:
            cache.pop(mid, None)
        return JSONResponse({"enrolled": True, "already": False, "url": url}, 201)

    return r
