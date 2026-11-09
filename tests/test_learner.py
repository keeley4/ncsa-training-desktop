"""My Desktop progress and enroll-on-launch."""
import os

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.auth import MemoryAuthStore
from harness.contract import response_schema, validator_for
from harness.learner import MemoryEnrolLog
from harness.moodle import MoodleError
from harness.store import FileStore
from tests.test_admin import BASE, signin
from tests.test_auth import CFG, FakeGlobus
from tests.test_crosswalk import FakeMoodle, u

SITE = "https://moodle.example.org"


class FakeLearn:
    """Moodle web services used by the learner features, for one learner (Moodle id 7)."""

    def __init__(self):
        self.enrolled = {2: {"progress": 40.0, "completed": False, "lastaccess": 1_700_000_000},
                         4: {"progress": None, "completed": None, "lastaccess": 0},
                         77: {"progress": 100, "completed": True, "lastaccess": 1_800_000_000}}   # not on the front door
        self.methods = {3: [{"type": "self", "status": "1"}], 9: [{"type": "guest", "status": "1"}],
                        6: [{"type": "self", "status": "1", "wsfunction": "enrol_self_get_instance_info"}],
                        5: [{"type": "self", "status": "Enrolments are closed"}]}
        self.calls = []
        self.down = False
        self.refuse = None

    def __call__(self, fn, **p):
        self.calls.append((fn, p))
        if self.down:
            raise MoodleError(fn, "network", "down")
        if fn == "core_enrol_get_users_courses":
            assert p["userid"] == 7
            return [{"id": cid, "fullname": f"Course {cid}", "visible": 1, **v} for cid, v in self.enrolled.items()]
        if fn == "core_enrol_get_course_enrolment_methods":
            return self.methods.get(p["courseid"], [])
        if fn == "enrol_manual_enrol_users":
            if self.refuse:
                raise MoodleError(fn, self.refuse, "refused")
            (e,) = p["enrolments"]
            assert e["userid"] == 7 and e["roleid"] == 5
            self.enrolled[e["courseid"]] = {"progress": 0, "completed": False, "lastaccess": 0}
            return None
        raise AssertionError(fn)


@pytest.fixture
def env(built_site, monkeypatch):
    monkeypatch.delenv("MOODLE_URL", raising=False)
    monkeypatch.setenv("ENROL_ON_LAUNCH", "1")
    store, fake, learn, log = MemoryAuthStore(), FakeGlobus(), FakeLearn(), MemoryEnrolLog()
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=FakeMoodle(by_username={"ada@illinois.edu": u(7)}), admin_usernames=set(),
                     moodle_call=learn, enrol_log=log)
    c = TestClient(app, base_url=BASE)
    signin(c, fake)                                               # Ada -> Moodle account 7, linked
    return c, learn, log, store, fake


def post(c, cid, origin=BASE):
    return c.post("/api/v1/enrollments", json={"courseId": cid}, headers={"Origin": origin} if origin else {})


def test_progress_most_recent_first_and_matches_contract(env):
    c, *_ = env
    r = c.get("/api/v1/me/progress")
    body = r.json()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert body["status"] == "linked"
    assert [i["courseId"] for i in body["items"]] == [77, 2, 4]
    first = body["items"][1]
    assert first == {"courseId": 2, "title": "Course 2", "url": f"{SITE}/course/view.php?id=2", "tileId": "course-2",
                     "progress": 40, "completed": False, "lastAccess": "2023-11-14T22:13:20+00:00"}
    assert body["items"][0]["tileId"] is None and body["items"][2]["lastAccess"] is None
    assert list(validator_for(response_schema("/me/progress")).iter_errors(body)) == []


def test_progress_needs_sign_in_and_link(env, built_site):
    c, learn, *_ = env
    assert TestClient(c.app, base_url=BASE).get("/api/v1/me/progress").status_code == 401
    store, fake = MemoryAuthStore(), FakeGlobus()
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=FakeMoodle(), admin_usernames=set(), moodle_call=learn)
    other = TestClient(app, base_url=BASE)
    signin(other, fake)                                           # no Moodle account -> none
    assert other.get("/api/v1/me/progress").json() == {"status": "none", "items": []}


def test_progress_unavailable_when_moodle_down(env, built_site):
    store, fake, learn = MemoryAuthStore(), FakeGlobus(), FakeLearn()
    learn.down = True
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=FakeMoodle(by_username={"ada@illinois.edu": u(7)}), admin_usernames=set(), moodle_call=learn)
    c = TestClient(app, base_url=BASE)
    signin(c, fake)
    assert c.get("/api/v1/me/progress").json() == {"status": "unavailable", "items": []}


def test_enrols_in_open_course_then_progress_shows_it(env):
    c, learn, log, *_ = env
    r = post(c, 3)
    assert r.status_code == 201 and r.json() == {"enrolled": True, "already": False, "url": f"{SITE}/course/view.php?id=3"}
    assert [x[3] for x in log.rows] == ["enrolled"] and log.rows[0][1:3] == (7, 3)
    assert 3 in [i["courseId"] for i in c.get("/api/v1/me/progress").json()["items"]]
    again = post(c, 3)
    assert again.status_code == 200 and again.json()["already"] is True
    assert sum(1 for f, _ in learn.calls if f == "enrol_manual_enrol_users") == 1


@pytest.mark.parametrize("cid,status,code", [
    (9, 409, "not_open"),        # guest-only course
    (6, 409, "not_open"),        # self-enrolment needs a key
    (5, 409, "not_open"),        # self-enrolment closed
    (999, 404, "not_in_catalog"),
    (77, 404, "not_in_catalog"),
])
def test_never_opens_a_closed_or_unknown_course(env, cid, status, code):
    c, learn, log, *_ = env
    r = post(c, cid)
    assert r.status_code == status and r.json()["error"] == code and r.json()["enrolled"] is False
    assert r.json()["url"].endswith(f"/course/view.php?id={cid}")
    assert not any(f == "enrol_manual_enrol_users" for f, _ in learn.calls)


def test_guards(env, monkeypatch):
    c, learn, *_ = env
    assert TestClient(c.app, base_url=BASE).post("/api/v1/enrollments", json={"courseId": 3},
                                                 headers={"Origin": BASE}).status_code == 401
    assert post(c, 3, origin="https://evil.example").status_code == 403
    assert post(c, 3, origin=None).status_code == 403
    assert c.post("/api/v1/enrollments", content=b"nope", headers={"Origin": BASE}).status_code == 400
    monkeypatch.setenv("ENROL_ON_LAUNCH", "0")
    assert post(c, 3).json()["error"] == "enrol_off"
    assert not any(f == "enrol_manual_enrol_users" for f, _ in learn.calls)


def test_not_linked_person_is_sent_to_moodle(env):
    c, learn, log, store, fake = env
    uid = next(iter(store.links))
    store.links[uid].update(status="review", moodleId=None)
    assert post(c, 3).json()["error"] == "not_linked"


def test_rate_limit_per_person(env, monkeypatch):
    c, learn, log, *_ = env
    monkeypatch.setenv("ENROL_LIMIT_HOUR", "1")
    assert post(c, 3).status_code == 201
    learn.methods[2] = [{"type": "self", "status": "1"}]
    learn.enrolled.pop(2)
    r = post(c, 2)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert [x[3] for x in log.rows] == ["enrolled", "rate_limited"]


def test_moodle_refusal_is_logged_and_falls_back(env):
    c, learn, log, *_ = env
    learn.refuse = "wsusercannotassign"
    r = post(c, 3)
    assert r.status_code == 502 and r.json()["error"] == "moodle_refused"
    assert log.rows[-1][3] == "error:wsusercannotassign"


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_pg_enrol_log_counts():
    from harness.auth import PgAuthStore
    from harness.learner import PgEnrolLog
    dsn = os.environ["TEST_DATABASE_URL"]
    PgAuthStore(dsn).migrate()
    log = PgEnrolLog(dsn)
    before = log.count_enrolments(None, 3600)
    log.log_enrolment(None, 7, 3, "enrolled")
    log.log_enrolment(None, 7, 4, "rate_limited")
    assert log.count_enrolments(None, 3600) == before + 1
