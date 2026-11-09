"""Identity crosswalk: which Learn@NCSA (Moodle) account belongs to a person who signed in with Globus.

Rules (see claude/identity-crosswalk-findings.md):
  1. Any linked Globus identity username equal to a Moodle username of a Globus-created account
     (auth=oauth2), not suspended, and exactly one such account  ->  "linked" (automatic).
  2. Username matches that are not safe to trust (self-registered account, suspended account,
     several accounts)  ->  "review".
  3. Only an email matches  ->  "review" (never automatic: email accounts can be self-registered).
  4. Nothing matches  ->  "none" (the person has not used Learn@NCSA yet).
  Moodle unreachable or not configured  ->  "unavailable" (sign-in still succeeds).

Only the Moodle account id, auth type and suspended flag are kept; no other profile data is stored.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .moodle import MoodleClient, MoodleError

# Moodle's PARAM_USERNAME without extended characters; values outside this would make
# core_user_get_users_by_field reject the whole request.
_USERNAME = re.compile(r"^[a-z0-9_.@-]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

LookupFn = Callable[[str, list[str]], list[dict[str, Any]]]


@dataclass
class Candidate:
    moodle_id: int
    auth: str
    suspended: bool
    matched_by: str          # "username" or "email"

    def as_dict(self) -> dict[str, Any]:
        return {"moodleId": self.moodle_id, "auth": self.auth, "suspended": self.suspended,
                "matchedBy": self.matched_by}


@dataclass
class Result:
    status: str                              # linked | review | none | unavailable
    moodle_id: int | None = None
    method: str | None = None                # username (for linked)
    reason: str | None = None                # why review / unavailable
    candidates: list[Candidate] = field(default_factory=list)


def _usernames(profile_identities: Iterable[dict[str, Any]], preferred: str | None) -> list[str]:
    seen: list[str] = []
    for v in [preferred, *(i.get("username") for i in profile_identities)]:
        v = (v or "").strip().lower()
        if v and _USERNAME.match(v) and v not in seen:
            seen.append(v)
    return seen


def _emails(profile_identities: Iterable[dict[str, Any]], primary: str | None) -> list[str]:
    seen: list[str] = []
    for v in [primary, *(i.get("email") for i in profile_identities)]:
        v = (v or "").strip().lower()
        if v and _EMAIL.match(v) and v not in seen:
            seen.append(v)
    return seen


def _candidates(rows: list[dict[str, Any]], how: str) -> dict[int, Candidate]:
    out: dict[int, Candidate] = {}
    for r in rows or []:
        if "id" not in r:
            continue
        out[int(r["id"])] = Candidate(int(r["id"]), str(r.get("auth", "")), bool(r.get("suspended")), how)
    return out


def resolve(lookup: LookupFn, identities: list[dict[str, Any]], preferred_username: str | None,
            email: str | None) -> Result:
    try:
        names = _usernames(identities, preferred_username)
        by_name = _candidates(lookup("username", names), "username") if names else {}
        if by_name:
            good = [c for c in by_name.values() if c.auth == "oauth2" and not c.suspended]
            if len(by_name) == 1 and len(good) == 1:
                return Result("linked", good[0].moodle_id, "username", candidates=good)
            if len(by_name) > 1:
                reason = "several_accounts"
            elif any(c.suspended for c in by_name.values()):
                reason = "suspended_account"
            else:
                reason = "not_globus_account"
            return Result("review", reason=reason, candidates=list(by_name.values()))
        mails = _emails(identities, email)
        by_mail = _candidates(lookup("email", mails), "email") if mails else {}
        if by_mail:
            return Result("review", reason="email_only", candidates=list(by_mail.values()))
        return Result("none")
    except MoodleError as exc:
        return Result("unavailable", reason=exc.errorcode)


def moodle_lookup_from_env() -> LookupFn | None:
    """Lookup function using the IP-restricted user-lookup token, or None if not configured."""
    site = os.environ.get("MOODLE_URL", "").strip()
    token = os.environ.get("MOODLE_LOOKUP_TOKEN", "").strip()
    tf = os.environ.get("MOODLE_LOOKUP_TOKEN_FILE")
    if not token and tf and Path(tf).expanduser().exists():
        token = Path(tf).expanduser().read_text().strip()
    if not (site and token):
        return None
    client = MoodleClient(site, token, timeout=15.0)

    def lookup(field_name: str, values: list[str]) -> list[dict[str, Any]]:
        return client.call("core_user_get_users_by_field", field=field_name, values=values)

    return lookup
