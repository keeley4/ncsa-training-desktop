"""Identity crosswalk rules, with a fake Moodle lookup."""
import pytest
from fastapi.testclient import TestClient

from harness.crosswalk import resolve
from harness.moodle import MoodleError

IDS = [{"sub": "s1", "username": "Ada@Illinois.edu", "email": "ada@illinois.edu"},
       {"sub": "s2", "username": "ada@orcid.org", "email": None},
       {"sub": "s3", "username": "bad name!", "email": "not-an-email"}]


class FakeMoodle:
    def __init__(self, by_username=None, by_email=None, fail=False):
        self.by = {"username": by_username or {}, "email": by_email or {}}
        self.fail = fail
        self.calls = []

    def __call__(self, field, values):
        self.calls.append((field, list(values)))
        if self.fail:
            raise MoodleError("core_user_get_users_by_field", "network", "down")
        return [self.by[field][v] for v in values if v in self.by[field]]


def u(i, auth="oauth2", suspended=0, **extra):
    return {"id": i, "auth": auth, "suspended": suspended, "firstname": "x", "city": "y", **extra}


def test_linked_by_identity_username_lowercased_and_filtered():
    m = FakeMoodle(by_username={"ada@illinois.edu": u(7)})
    r = resolve(m, IDS, "Ada@Illinois.edu", "ada@illinois.edu")
    assert (r.status, r.moodle_id, r.method) == ("linked", 7, "username")
    # lowercased, de-duplicated, invalid username left out (Moodle would reject the whole call)
    assert m.calls == [("username", ["ada@illinois.edu", "ada@orcid.org"])]


def test_only_id_auth_suspended_kept():
    r = resolve(FakeMoodle(by_username={"ada@illinois.edu": u(7)}), IDS, None, None)
    assert r.candidates[0].as_dict() == {"moodleId": 7, "auth": "oauth2", "suspended": False, "matchedBy": "username"}


@pytest.mark.parametrize("rows,reason", [
    ({"ada@illinois.edu": u(7), "ada@orcid.org": u(8)}, "several_accounts"),
    ({"ada@illinois.edu": u(7, suspended=1)}, "suspended_account"),
    ({"ada@illinois.edu": u(7, auth="email")}, "not_globus_account"),   # self-registered: never automatic
])
def test_unsafe_username_matches_go_to_review(rows, reason):
    r = resolve(FakeMoodle(by_username=rows), IDS, None, None)
    assert r.status == "review" and r.reason == reason and r.moodle_id is None


def test_email_only_match_goes_to_review():
    m = FakeMoodle(by_email={"ada@illinois.edu": u(9)})
    r = resolve(m, IDS, None, "ADA@illinois.edu")
    assert r.status == "review" and r.reason == "email_only" and [c.moodle_id for c in r.candidates] == [9]
    assert m.calls[1] == ("email", ["ada@illinois.edu"])


def test_no_match_and_unavailable():
    assert resolve(FakeMoodle(), IDS, None, None).status == "none"
    r = resolve(FakeMoodle(fail=True), IDS, None, None)
    assert r.status == "unavailable" and r.reason == "network"


def test_signin_records_link_and_me_hides_candidates(built_site):
    from harness.api import create_app
    from harness.auth import MemoryAuthStore
    from harness.contract import errors
    from harness.store import FileStore
    from tests.test_auth import CFG, FakeGlobus, start_login

    store, fake = MemoryAuthStore(), FakeGlobus()
    moodle = FakeMoodle(by_username={"ada@illinois.edu": u(7), "ada@orcid.org": u(8)})
    c = TestClient(create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG,
                              exchange=fake, lookup=moodle), base_url="https://training.example.edu")
    _, q = start_login(c, fake)
    c.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False)
    body = c.get("/api/v1/me").json()
    assert body["user"]["moodle"] == {"status": "review", "accountId": None}
    assert errors("me", body) == []
    assert "possible match" in c.get("/auth").text
    assert [x["moodleId"] for x in store.links[1]["candidates"]] == [7, 8]   # kept server-side only


def test_signin_survives_moodle_outage(built_site):
    from harness.api import create_app
    from harness.auth import MemoryAuthStore
    from harness.store import FileStore
    from tests.test_auth import CFG, FakeGlobus, start_login

    store, fake = MemoryAuthStore(), FakeGlobus()

    def boom(field, values):
        raise RuntimeError("unexpected")

    c = TestClient(create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG,
                              exchange=fake, lookup=boom), base_url="https://training.example.edu")
    _, q = start_login(c, fake)
    assert c.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False).status_code == 303
    assert c.get("/api/v1/me").json()["user"]["moodle"]["status"] == "unavailable"
