"""Administrator tools (Phase 3): the identity-crosswalk review queue.

Admins are listed by Globus identity username in ADMIN_USERNAMES (comma-separated), e.g.
ADMIN_USERNAMES=keeley4@illinois.edu. A signed-in person is an admin if any of their linked
identities (or preferred_username) is on the list.

  GET  /admin/review                      HTML queue (forms post back here)
  POST /admin/review/{userId}             form: action=link&moodleId=N | action=reject
  GET  /api/v1/crosswalk/review           JSON queue (admin only)
  POST /api/v1/crosswalk/review/{userId}  JSON {"action": "link", "moodleId": N} | {"action": "reject"}

POSTs must come from this site (Origin/Referer checked) on top of the SameSite=Lax session cookie.
"""
from __future__ import annotations

import html
import json
import os
import urllib.parse
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

REASON_TEXT = {
    "email_only": "Only the email address matches",
    "several_accounts": "Several Learn@NCSA accounts match",
    "suspended_account": "The matching account is suspended",
    "not_globus_account": "The matching account was not created through Globus (self-registered)",
}


def admin_usernames_from_env() -> set[str]:
    return {v.strip().lower() for v in os.environ.get("ADMIN_USERNAMES", "").split(",") if v.strip()}


def make_is_admin(get_admins: Callable[[], set[str]]) -> Callable[[dict[str, Any] | None], bool]:
    def is_admin(user: dict[str, Any] | None) -> bool:
        if not user:
            return False
        names = {(user.get("preferred_username") or "").lower()}
        names |= {(i.get("username") or "").lower() for i in user.get("identities") or []}
        names.discard("")
        return bool(names & get_admins())
    return is_admin


def _same_origin(request: Request, base_url: str | None) -> bool:
    src = request.headers.get("origin") or request.headers.get("referer")
    if not src:
        return False
    want = urllib.parse.urlsplit(base_url or str(request.base_url))
    got = urllib.parse.urlsplit(src)
    return (got.scheme, got.netloc) == (want.scheme, want.netloc)


def make_admin_router(store: Any, current_user: Callable[[Request], dict[str, Any] | None],
                      is_admin: Callable[[dict[str, Any] | None], bool],
                      get_base_url: Callable[[], str | None],
                      get_moodle_url: Callable[[], str | None]) -> APIRouter:
    r = APIRouter()

    def guard(request: Request, write: bool = False) -> tuple[dict[str, Any] | None, Response | None]:
        user = current_user(request)
        if not user:
            return None, JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        if not is_admin(user):
            return None, JSONResponse({"error": "forbidden", "message": "Administrators only."}, 403)
        if write and not _same_origin(request, get_base_url()):
            return None, JSONResponse({"error": "forbidden", "message": "Cross-site request refused."}, 403)
        return user, None

    def admin_name(user: dict[str, Any]) -> str:
        return user.get("preferred_username") or user.get("globus_sub") or "admin"

    def apply(user_id: int, action: str, moodle_id: Any, admin: str) -> tuple[bool, str]:
        if action == "link":
            try:
                moodle_id = int(moodle_id)
            except (TypeError, ValueError):
                return False, "A Moodle account id is required to link."
            allowed = {c.get("moodleId") for item in store.list_reviews() if item["userId"] == user_id
                       for c in item.get("candidates", [])}
            if moodle_id not in allowed:
                return False, "That account is not one of the candidates for this person."
        elif action != "reject":
            return False, "Unknown action."
        ok = store.decide(user_id, action, moodle_id if action == "link" else None, admin)
        return ok, "Done." if ok else "This item is no longer waiting for review."

    # ---------- JSON API ----------
    @r.get("/api/v1/crosswalk/review")
    def api_list(request: Request) -> Response:
        _, err = guard(request)
        if err:
            return err
        return JSONResponse({"items": store.list_reviews()}, headers={"Cache-Control": "no-store"})

    @r.post("/api/v1/crosswalk/review/{user_id}")
    async def api_decide(user_id: int, request: Request) -> Response:
        user, err = guard(request, write=True)
        if err:
            return err
        try:
            body = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            return JSONResponse({"error": "bad_request", "message": "Body must be JSON."}, 400)
        ok, msg = apply(user_id, str(body.get("action", "")), body.get("moodleId"), admin_name(user))
        return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 409)

    # ---------- HTML page ----------
    @r.get("/admin/review", include_in_schema=False)
    def page(request: Request, msg: str | None = None) -> Response:
        user, err = guard(request)
        if err:
            if err.status_code == 401:
                return RedirectResponse("/auth/login?next=/admin/review", status_code=303)
            return HTMLResponse("<p>Administrators only.</p>", status_code=403)
        site = (get_moodle_url() or "").rstrip("/")
        rows = []
        for item in store.list_reviews():
            cands = []
            for c in item.get("candidates", []):
                mid = int(c.get("moodleId"))
                profile = (f' <a href="{html.escape(site)}/user/profile.php?id={mid}" target="_blank" rel="noopener">'
                           f'view in Learn@NCSA</a>') if site else ""
                flags = ", ".join(x for x in [c.get("auth") or "", "suspended" if c.get("suspended") else "",
                                              f"matched by {c.get('matchedBy')}"] if x)
                cands.append(
                    f'<li>Account {mid} <small>({html.escape(flags)})</small>{profile}'
                    f'<form method="post" action="/admin/review/{item["userId"]}" class="inline">'
                    f'<input type="hidden" name="action" value="link"><input type="hidden" name="moodleId" value="{mid}">'
                    f'<button>Link to this account</button></form></li>')
            rows.append(
                f'<section><h2>{html.escape(item.get("name") or "(no name)")}</h2>'
                f'<p>{html.escape(item.get("username") or "")} · {html.escape(item.get("email") or "")}</p>'
                f'<p><strong>{html.escape(REASON_TEXT.get(item.get("reason") or "", item.get("reason") or ""))}</strong></p>'
                f'<ul>{"".join(cands)}</ul>'
                f'<form method="post" action="/admin/review/{item["userId"]}"><input type="hidden" name="action" value="reject">'
                f'<button>None of these: no link</button></form></section>')
        note = f'<p role="status">{html.escape(msg)}</p>' if msg else ""
        body = "".join(rows) or "<p>Nothing is waiting for review.</p>"
        doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
               '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
               '<title>Account review (prototype)</title><style>'
               'body{font:16px/1.5 system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 16px}'
               'section{border-top:1px solid #ccc;padding:12px 0}form.inline{display:inline;margin-left:8px}'
               'button{font:inherit;padding:4px 10px}</style></head><body>'
               f'<h1>Learn@NCSA account review</h1>{note}'
               '<p>People who signed in with Globus where the matching Learn@NCSA account could not be linked automatically. '
               'Your decision is kept; later sign-ins do not change it.</p>'
               f'{body}<p><a href="/auth">Back</a></p></body></html>')
        return HTMLResponse(doc, headers={"Cache-Control": "no-store"})

    @r.post("/admin/review/{user_id}", include_in_schema=False)
    async def page_decide(user_id: int, request: Request) -> Response:
        user, err = guard(request, write=True)
        if err:
            return err
        form = urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"))
        ok, msg = apply(user_id, (form.get("action") or [""])[0], (form.get("moodleId") or [None])[0],
                        admin_name(user))
        return RedirectResponse("/admin/review?" + urllib.parse.urlencode({"msg": msg}), status_code=303)

    return r
