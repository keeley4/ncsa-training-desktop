"""Administrator review queue for identity-crosswalk matches."""
import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.auth import MemoryAuthStore
from harness.store import FileStore
from tests.test_auth import CFG, FakeGlobus, claims_for, start_login
from tests.test_crosswalk import FakeMoodle, u

BASE = "https://training.example.edu"


def signin(client, fake, **claims):
    _, q = start_login(client, fake)
    fake.claims_override = claims
    client.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False)


@pytest.fixture
def env(built_site):
    store, fake = MemoryAuthStore(), FakeGlobus()
    moodle = FakeMoodle(by_username={"ada@illinois.edu": u(7), "ada@orcid.org": u(8)})
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=moodle, admin_usernames={"Boss@Illinois.edu"})
    learner = TestClient(app, base_url=BASE)
    signin(learner, fake)                                         # Ada -> two accounts -> review
    admin = TestClient(app, base_url=BASE)
    signin(admin, fake, sub="globus-sub-9", name="Boss", preferred_username="boss@illinois.edu",
           identity_set=[{"sub": "globus-sub-9", "username": "boss@illinois.edu"}])
    return learner, admin, store, fake


def test_only_admins_see_the_queue(env):
    learner, admin, store, _ = env
    assert learner.get("/api/v1/crosswalk/review").status_code == 403
    anon = TestClient(learner.app, base_url=BASE)
    assert anon.get("/api/v1/crosswalk/review").status_code == 401
    assert anon.get("/admin/review", follow_redirects=False).headers["location"].startswith("/auth/login")
    items = admin.get("/api/v1/crosswalk/review").json()["items"]
    assert [i["name"] for i in items] == ["Ada Lovelace"]
    assert sorted(c["moodleId"] for c in items[0]["candidates"]) == [7, 8]
    assert "Account review queue" in admin.get("/auth").text and "Account review queue" not in learner.get("/auth").text


def test_admin_links_a_candidate_and_decision_sticks(env):
    learner, admin, store, fake = env
    uid = admin.get("/api/v1/crosswalk/review").json()["items"][0]["userId"]
    r = admin.post(f"/api/v1/crosswalk/review/{uid}", json={"action": "link", "moodleId": 8}, headers={"Origin": BASE})
    assert r.status_code == 200 and r.json()["ok"]
    assert learner.get("/api/v1/me").json()["user"]["moodle"] == {"status": "linked", "accountId": 8}
    assert admin.get("/api/v1/crosswalk/review").json()["items"] == []
    signin(learner, fake)                                         # signing in again does not undo it
    assert learner.get("/api/v1/me").json()["user"]["moodle"] == {"status": "linked", "accountId": 8}
    assert store.links[uid]["decidedBy"] == "boss@illinois.edu"


def test_link_only_to_a_candidate_and_only_same_site(env):
    learner, admin, store, _ = env
    uid = admin.get("/api/v1/crosswalk/review").json()["items"][0]["userId"]
    assert admin.post(f"/api/v1/crosswalk/review/{uid}", json={"action": "link", "moodleId": 999},
                      headers={"Origin": BASE}).status_code == 409
    assert admin.post(f"/api/v1/crosswalk/review/{uid}", json={"action": "reject"},
                      headers={"Origin": "https://evil.example"}).status_code == 403
    assert admin.post(f"/api/v1/crosswalk/review/{uid}", json={"action": "reject"}).status_code == 403  # no origin
    assert learner.post(f"/api/v1/crosswalk/review/{uid}", json={"action": "reject"},
                        headers={"Origin": BASE}).status_code == 403


def test_html_page_reject_flow_and_escaping(env, built_site):
    learner, admin, store, fake = env
    page = admin.get("/admin/review").text
    assert "Several Learn@NCSA accounts match" in page and "Link to this account" in page
    uid = admin.get("/api/v1/crosswalk/review").json()["items"][0]["userId"]
    r = admin.post(f"/admin/review/{uid}", content="action=reject", follow_redirects=False,
                   headers={"Origin": BASE, "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303 and "Done" in r.headers["location"]
    assert learner.get("/api/v1/me").json()["user"]["moodle"]["status"] == "none"
    assert "Nothing is waiting for review" in admin.get("/admin/review").text


@pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_postgres_review_queue(result):
    import os
    from harness.auth import PgAuthStore, Profile
    from harness.crosswalk import Candidate, Result
    from harness.store import PgStore
    PgStore(os.environ["TEST_DATABASE_URL"]).migrate()
    s = PgAuthStore(os.environ["TEST_DATABASE_URL"])
    uid = s.upsert_user(Profile(globus_sub="pg-review-1", name="Grace", preferred_username="grace@illinois.edu"))
    s.set_link(uid, Result("review", reason="email_only", candidates=[Candidate(42, "email", False, "email")]))
    item = [i for i in s.list_reviews() if i["userId"] == uid][0]
    assert item["reason"] == "email_only" and item["candidates"][0]["moodleId"] == 42
    assert s.decide(uid, "link", 42, "boss@illinois.edu") is True
    assert s.decide(uid, "reject", None, "boss@illinois.edu") is False          # no longer in review
    assert s.get_link(uid) == {"status": "linked", "moodleId": 42, "method": "admin", "reason": None}
