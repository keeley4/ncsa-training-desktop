"""Globus sign-in (OIDC authorization code + PKCE, confidential client).

Routes (outside /api/v1 because they are browser redirects, not API documents):
  GET  /auth/login?next=/path   -> redirect to Globus
  GET  /auth/callback           -> code exchange, session cookie, redirect to next
  POST /auth/logout             -> end the session here
API:
  GET  /api/v1/me               -> {"signedIn": false} or the signed-in user's profile projection

ID token trust: the token is received directly from the Globus token endpoint over TLS, authenticated
with the client secret, so its signature check may be skipped (OIDC Core 3.1.3.7, item 6). The issuer,
audience, expiry and nonce are still checked. No Globus tokens are kept after login.

Configuration (environment; the secret comes from a 0600 file):
  GLOBUS_CLIENT_ID, GLOBUS_CLIENT_SECRET_FILE (or GLOBUS_CLIENT_SECRET),
  PUBLIC_BASE_URL (e.g. https://trainingdesktop.web.illinois.edu), optional OIDC_ISSUER,
  SESSION_HOURS (default 12).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from fastapi import APIRouter, Request
import html

from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

SCHEMA_SQL = Path(__file__).resolve().parent / "db" / "schema_auth.sql"
COOKIE = "__Host-ncsa_session"
LOGIN_TTL = 600            # seconds a login may take
CLOCK_SKEW = 120           # seconds tolerated on exp/iat


# ---------------------------------------------------------------- configuration

@dataclass
class AuthConfig:
    client_id: str
    client_secret: str
    base_url: str
    issuer: str = "https://auth.globus.org"
    authorize_url: str = "https://auth.globus.org/v2/oauth2/authorize"
    token_url: str = "https://auth.globus.org/v2/oauth2/token"
    scopes: str = "openid profile email"
    session_hours: int = 12

    @property
    def redirect_uri(self) -> str:
        return self.base_url.rstrip("/") + "/auth/callback"

    @classmethod
    def from_env(cls) -> "AuthConfig | None":
        cid = os.environ.get("GLOBUS_CLIENT_ID", "").strip()
        secret = os.environ.get("GLOBUS_CLIENT_SECRET", "").strip()
        sf = os.environ.get("GLOBUS_CLIENT_SECRET_FILE")
        if not secret and sf and Path(sf).expanduser().exists():
            secret = Path(sf).expanduser().read_text().strip()
        base = os.environ.get("PUBLIC_BASE_URL", "").strip()
        if not (cid and secret and base):
            return None
        issuer = os.environ.get("OIDC_ISSUER", "https://auth.globus.org").rstrip("/")
        return cls(client_id=cid, client_secret=secret, base_url=base, issuer=issuer,
                   session_hours=int(os.environ.get("SESSION_HOURS", "12")))


# ---------------------------------------------------------------- storage

@dataclass
class Profile:
    globus_sub: str
    name: str = ""
    email: str | None = None
    preferred_username: str | None = None
    organization: str | None = None
    identity_provider: str | None = None
    identities: list[dict[str, Any]] = field(default_factory=list)


class AuthStore(Protocol):
    def put_login(self, state: str, nonce: str, verifier: str, next_path: str) -> None: ...
    def take_login(self, state: str) -> tuple[str, str, str] | None: ...
    def upsert_user(self, p: Profile) -> int: ...
    def create_session(self, id_hash: str, user_id: int, expires: datetime) -> None: ...
    def session_user(self, id_hash: str) -> dict[str, Any] | None: ...
    def delete_session(self, id_hash: str) -> None: ...
    def set_link(self, user_id: int, result: Any) -> None: ...
    def get_link(self, user_id: int) -> dict[str, Any] | None: ...
    def list_reviews(self) -> list[dict[str, Any]]: ...
    def decide(self, user_id: int, action: str, moodle_id: int | None, admin: str) -> bool: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryAuthStore:
    """For tests and the static-file mode."""

    def __init__(self) -> None:
        self.logins: dict[str, tuple[str, str, str, float]] = {}
        self.users: dict[int, dict[str, Any]] = {}
        self.by_sub: dict[str, int] = {}
        self.sessions: dict[str, tuple[int, datetime]] = {}
        self.links: dict[int, dict[str, Any]] = {}

    def put_login(self, state, nonce, verifier, next_path):
        cutoff = time.time() - LOGIN_TTL
        self.logins = {k: v for k, v in self.logins.items() if v[3] >= cutoff}
        self.logins[state] = (nonce, verifier, next_path, time.time())

    def take_login(self, state):
        v = self.logins.pop(state, None)
        if not v or v[3] < time.time() - LOGIN_TTL:
            return None
        return v[0], v[1], v[2]

    def upsert_user(self, p):
        uid = self.by_sub.get(p.globus_sub) or len(self.users) + 1
        self.by_sub[p.globus_sub] = uid
        created = self.users.get(uid, {}).get("createdAt", _now().isoformat())
        self.users[uid] = {"id": uid, "createdAt": created, "lastLoginAt": _now().isoformat(), **p.__dict__}
        return uid

    def create_session(self, id_hash, user_id, expires):
        self.sessions[id_hash] = (user_id, expires)

    def session_user(self, id_hash):
        s = self.sessions.get(id_hash)
        if not s or s[1] <= _now():
            return None
        return {**self.users[s[0]], "moodle": self.links.get(s[0])}

    def delete_session(self, id_hash):
        self.sessions.pop(id_hash, None)

    def set_link(self, user_id, result):
        self.links[user_id] = {"status": result.status, "moodleId": result.moodle_id, "method": result.method,
                               "reason": result.reason, "candidates": [c.as_dict() for c in result.candidates],
                               "checkedAt": _now().isoformat()}

    def get_link(self, user_id):
        return self.links.get(user_id)

    def list_reviews(self):
        out = []
        for uid, link in sorted(self.links.items()):
            if link["status"] == "review":
                u = self.users[uid]
                out.append({"userId": uid, "name": u.get("name") or "", "username": u.get("preferred_username"),
                            "email": u.get("email"), "reason": link.get("reason"),
                            "candidates": link.get("candidates", []), "checkedAt": link.get("checkedAt")})
        return out

    def link_counts(self):
        out: dict[str, int] = {}
        for link in self.links.values():
            out[link["status"]] = out.get(link["status"], 0) + 1
        return out

    def decide(self, user_id, action, moodle_id, admin):
        link = self.links.get(user_id)
        if not link or link["status"] != "review":
            return False
        if action == "link":
            link.update(status="linked", moodleId=moodle_id, method="admin", reason=None)
        else:
            link.update(status="none", moodleId=None, method="admin", reason="admin_rejected")
        link.update(decidedBy=admin, decidedAt=_now().isoformat())
        return True


class PgAuthStore:
    def __init__(self, dsn: str):
        import psycopg

        self._psycopg = psycopg
        self.dsn = dsn

    def _conn(self):
        return self._psycopg.connect(self.dsn, autocommit=False)

    def migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(SCHEMA_SQL.read_text())
            conn.commit()

    def put_login(self, state, nonce, verifier, next_path):
        with self._conn() as conn:
            conn.execute("DELETE FROM login_requests WHERE created_at < now() - %s * interval '1 second'", (LOGIN_TTL,))
            conn.execute("INSERT INTO login_requests (state, nonce, verifier, next_path) VALUES (%s, %s, %s, %s)",
                         (state, nonce, verifier, next_path))
            conn.commit()

    def take_login(self, state):
        with self._conn() as conn:
            row = conn.execute(
                "DELETE FROM login_requests WHERE state=%s AND created_at >= now() - %s * interval '1 second' "
                "RETURNING nonce, verifier, next_path", (state, LOGIN_TTL)).fetchone()
            conn.commit()
        return tuple(row) if row else None

    def upsert_user(self, p):
        with self._conn() as conn:
            uid = conn.execute(
                """INSERT INTO users (globus_sub, name, email, preferred_username, organization, identity_provider)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (globus_sub) DO UPDATE SET name=EXCLUDED.name, email=EXCLUDED.email,
                     preferred_username=EXCLUDED.preferred_username, organization=EXCLUDED.organization,
                     identity_provider=EXCLUDED.identity_provider, last_login_at=now()
                   RETURNING id""",
                (p.globus_sub, p.name, p.email, p.preferred_username, p.organization, p.identity_provider),
            ).fetchone()[0]
            conn.execute("DELETE FROM user_identities WHERE user_id=%s", (uid,))
            for i in p.identities:
                conn.execute(
                    "INSERT INTO user_identities (user_id, sub, username, email, identity_provider, organization) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (uid, i["sub"], i.get("username", ""), i.get("email"), i.get("identityProvider"),
                     i.get("organization")))
            conn.execute("DELETE FROM sessions WHERE expires_at < now()")
            conn.commit()
        return uid

    def create_session(self, id_hash, user_id, expires):
        with self._conn() as conn:
            conn.execute("INSERT INTO sessions (id_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                         (id_hash, user_id, expires))
            conn.commit()

    def session_user(self, id_hash):
        with self._conn() as conn:
            row = conn.execute(
                """SELECT u.id, u.globus_sub, u.name, u.email, u.preferred_username, u.organization,
                          u.identity_provider, u.created_at, u.last_login_at
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.id_hash=%s AND s.expires_at > now()""", (id_hash,)).fetchone()
            if not row:
                return None
            ids = conn.execute(
                "SELECT sub, username, email, identity_provider, organization FROM user_identities "
                "WHERE user_id=%s ORDER BY username", (row[0],)).fetchall()
            link = conn.execute("SELECT status, moodle_id, method, reason FROM moodle_links WHERE user_id=%s",
                                (row[0],)).fetchone()
        return {
            "id": row[0], "globus_sub": row[1], "name": row[2], "email": row[3], "preferred_username": row[4],
            "organization": row[5], "identity_provider": row[6], "createdAt": row[7].isoformat(),
            "lastLoginAt": row[8].isoformat(),
            "identities": [{"sub": r[0], "username": r[1], "email": r[2], "identityProvider": r[3],
                            "organization": r[4]} for r in ids],
            "moodle": ({"status": link[0], "moodleId": link[1], "method": link[2], "reason": link[3]}
                       if link else None),
        }

    def delete_session(self, id_hash):
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id_hash=%s", (id_hash,))
            conn.commit()

    def set_link(self, user_id, result):
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO moodle_links (user_id, status, moodle_id, method, reason, candidates, checked_at)
                   VALUES (%s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (user_id) DO UPDATE SET status=EXCLUDED.status, moodle_id=EXCLUDED.moodle_id,
                     method=EXCLUDED.method, reason=EXCLUDED.reason, candidates=EXCLUDED.candidates,
                     checked_at=now()""",
                (user_id, result.status, result.moodle_id, result.method, result.reason,
                 Jsonb([c.as_dict() for c in result.candidates])))
            conn.commit()

    def get_link(self, user_id):
        with self._conn() as conn:
            row = conn.execute("SELECT status, moodle_id, method, reason FROM moodle_links WHERE user_id=%s",
                               (user_id,)).fetchone()
        return {"status": row[0], "moodleId": row[1], "method": row[2], "reason": row[3]} if row else None

    def list_reviews(self):
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT u.id, u.name, u.preferred_username, u.email, l.reason, l.candidates, l.checked_at
                   FROM moodle_links l JOIN users u ON u.id = l.user_id
                   WHERE l.status = 'review' ORDER BY l.checked_at""").fetchall()
        return [{"userId": r[0], "name": r[1] or "", "username": r[2], "email": r[3], "reason": r[4],
                 "candidates": r[5] or [], "checkedAt": r[6].isoformat()} for r in rows]

    def link_counts(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT status, count(*) FROM moodle_links GROUP BY status").fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def decide(self, user_id, action, moodle_id, admin):
        with self._conn() as conn:
            if action == "link":
                cur = conn.execute(
                    """UPDATE moodle_links SET status='linked', moodle_id=%s, method='admin', reason=NULL,
                         decided_by=%s, decided_at=now() WHERE user_id=%s AND status='review'""",
                    (moodle_id, admin, user_id))
            else:
                cur = conn.execute(
                    """UPDATE moodle_links SET status='none', moodle_id=NULL, method='admin',
                         reason='admin_rejected', decided_by=%s, decided_at=now()
                       WHERE user_id=%s AND status='review'""", (admin, user_id))
            conn.commit()
            return cur.rowcount == 1


# ---------------------------------------------------------------- OIDC helpers

class AuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def hash_session(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safe_next(value: str | None, default: str = "/auth") -> str:
    """Only same-site absolute paths: '/x', never '//host' or 'https://...'."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as exc:  # noqa: BLE001
        raise AuthError("unreadable id_token") from exc


def check_id_token(claims: dict[str, Any], cfg: AuthConfig, nonce: str, now: float | None = None) -> None:
    now = now or time.time()
    if claims.get("iss", "").rstrip("/") != cfg.issuer:
        raise AuthError("wrong issuer")
    aud = claims.get("aud")
    if cfg.client_id not in (aud if isinstance(aud, list) else [aud]):
        raise AuthError("wrong audience")
    if float(claims.get("exp", 0)) < now - CLOCK_SKEW:
        raise AuthError("id_token expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise AuthError("nonce mismatch")
    if not claims.get("sub"):
        raise AuthError("no subject")


def profile_from_claims(c: dict[str, Any]) -> Profile:
    identities = []
    for i in c.get("identity_set") or []:
        if isinstance(i, dict) and i.get("sub"):
            identities.append({
                "sub": i["sub"],
                "username": i.get("username") or "",
                "email": i.get("email"),
                "identityProvider": i.get("identity_provider_display_name") or i.get("identity_provider"),
                "organization": i.get("organization"),
            })
    return Profile(
        globus_sub=c["sub"], name=c.get("name") or "", email=c.get("email"),
        preferred_username=c.get("preferred_username"), organization=c.get("organization"),
        identity_provider=c.get("identity_provider_display_name"), identities=identities,
    )


def http_token_exchange(cfg: AuthConfig, code: str, verifier: str) -> dict[str, Any]:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": cfg.redirect_uri, "code_verifier": verifier,
    }).encode()
    basic = base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()
    req = urllib.request.Request(cfg.token_url, data=body, method="POST", headers={
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json", "User-Agent": "ncsa-training-harness",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise AuthError(f"token endpoint returned {exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AuthError("token endpoint unreachable") from exc


# ---------------------------------------------------------------- routes

LINK_TEXT = {
    "linked": "found and linked.",
    "review": "possible match; waiting for an administrator to confirm.",
    "none": "none yet. One is created the first time you sign in to Learn@NCSA.",
    "unavailable": "could not be checked right now.",
}

def me_document(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {"signedIn": False}
    return {"signedIn": True, "user": {
        "globusId": user["globus_sub"],
        "name": user.get("name") or "",
        "email": user.get("email"),
        "username": user.get("preferred_username"),
        "organization": user.get("organization"),
        "identityProvider": user.get("identity_provider"),
        "identities": user.get("identities") or [],
        "lastLoginAt": user.get("lastLoginAt"),
        "moodle": moodle_projection(user.get("moodle")),
    }}


def moodle_projection(link: dict[str, Any] | None) -> dict[str, Any]:
    """What the signed-in person may see about their own crosswalk result (never other candidates)."""
    if not link:
        return {"status": "unavailable", "accountId": None}
    return {"status": link["status"], "accountId": link.get("moodleId") if link["status"] == "linked" else None}


def make_router(get_cfg: Callable[[], AuthConfig | None], store: AuthStore,
                exchange: Callable[[AuthConfig, str, str], dict[str, Any]] = http_token_exchange,
                linker: Callable[[Profile], Any] | None = None,
                is_admin: Callable[[dict[str, Any]], bool] = lambda u: False,
                on_signin: Callable[[], None] | None = None) -> APIRouter:
    r = APIRouter()

    def current_user(request: Request) -> dict[str, Any] | None:
        value = request.cookies.get(COOKIE)
        return store.session_user(hash_session(value)) if value else None

    def fail(message: str, status: int = 400) -> Response:
        return JSONResponse({"error": "signin_failed", "message": message}, status_code=status)

    @r.get("/auth/login", include_in_schema=False)
    def login(next: str | None = None) -> Response:
        cfg = get_cfg()
        if cfg is None:
            return JSONResponse({"error": "signin_unavailable", "message": "Sign-in is not configured."}, 503)
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        store.put_login(state, nonce, verifier, safe_next(next))
        q = urllib.parse.urlencode({
            "client_id": cfg.client_id, "response_type": "code", "redirect_uri": cfg.redirect_uri,
            "scope": cfg.scopes, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return RedirectResponse(f"{cfg.authorize_url}?{q}", status_code=302,
                                headers={"Cache-Control": "no-store"})

    @r.get("/auth/callback", include_in_schema=False)
    def callback(state: str | None = None, code: str | None = None, error: str | None = None) -> Response:
        cfg = get_cfg()
        if cfg is None:
            return fail("Sign-in is not configured.", 503)
        pending = store.take_login(state) if state else None
        if pending is None:
            return fail("This sign-in link expired or was already used. Please sign in again.")
        nonce, verifier, next_path = pending
        if error or not code:
            return fail("Sign-in was cancelled or refused by Globus.")
        try:
            tokens = exchange(cfg, code, verifier)
            claims = decode_jwt_payload(tokens.get("id_token", ""))
            check_id_token(claims, cfg, nonce)
        except AuthError as exc:
            return fail(f"Sign-in could not be completed ({exc}).", 502)
        profile = profile_from_claims(claims)
        uid = store.upsert_user(profile)
        prior = store.get_link(uid)
        if linker is not None and not (prior and prior.get("method") == "admin"):  # admin decisions stick
            try:  # best effort: a Moodle outage must not block sign-in
                store.set_link(uid, linker(profile))
            except Exception:  # noqa: BLE001
                from .crosswalk import Result
                store.set_link(uid, Result("unavailable", reason="error"))
        raw = secrets.token_urlsafe(32)
        store.create_session(hash_session(raw), uid, _now() + timedelta(hours=cfg.session_hours))
        if on_signin is not None:
            try:
                on_signin()
            except Exception:  # noqa: BLE001  analytics must never block sign-in
                pass
        resp = RedirectResponse(next_path, status_code=303, headers={"Cache-Control": "no-store"})
        resp.set_cookie(COOKIE, raw, max_age=cfg.session_hours * 3600, path="/", secure=True,
                        httponly=True, samesite="lax")
        return resp

    @r.post("/auth/logout", include_in_schema=False)
    def logout(request: Request) -> Response:
        """Ends the session here. The Globus session itself is left alone (Globus has its own logout)."""
        value = request.cookies.get(COOKIE)
        if value:
            store.delete_session(hash_session(value))
        resp = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    @r.get("/auth", include_in_schema=False)
    def status_page(request: Request) -> Response:
        """Plain sign-in status page for testing until the front door has a sign-in button."""
        user = current_user(request)
        configured = get_cfg() is not None
        if user:
            ids = "".join(f"<li>{html.escape(i.get('username') or '')} "
                          f"<small>({html.escape(i.get('identityProvider') or '')})</small></li>"
                          for i in user.get("identities") or [])
            body = (f"<p>Signed in as <strong>{html.escape(user.get('name') or '')}</strong> "
                    f"({html.escape(user.get('preferred_username') or '')}).</p>"
                    f"<p>Linked identities:</p><ul>{ids}</ul>"
                    f"<p>Learn@NCSA account: {html.escape(LINK_TEXT.get(me_document(user)['user']['moodle']['status'], ''))}</p>"
                    + ('<p>Admin: <a href="/admin/review">Account review queue</a> · <a href="/coord">Coordinator</a> · <a href="/admin/workshops">Workshops</a> · '
                       '<a href="/admin/analytics">Usage analytics</a></p>' if is_admin(user) else '')
                    + '<form method="post" action="/auth/logout"><button>Sign out</button></form>')
        elif configured:
            body = '<p>Not signed in.</p><p><a href="/auth/login">Sign in with Globus</a></p>'
        else:
            body = "<p>Sign-in is not configured on this server yet.</p>"
        doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
               '<meta name="viewport" content="width=device-width, initial-scale=1">'
               '<meta name="robots" content="noindex"><title>NCSA Training sign-in (prototype)</title>'
               '<style>body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}'
               'button{font:inherit;padding:6px 14px}</style></head><body>'
               f"<h1>NCSA Training sign-in</h1>{body}"
               '<p><small>Prototype. <a href="/api/v1/me">/api/v1/me</a></small></p></body></html>')
        return HTMLResponse(doc, headers={"Cache-Control": "no-store"})

    @r.get("/api/v1/me")
    def me(request: Request) -> Response:
        return JSONResponse(me_document(current_user(request)), headers={"Cache-Control": "no-store"})

    r.current_user = current_user  # type: ignore[attr-defined]
    return r
