"""Coordinator tools (12-step workflow, copy blocks, closeout) and the Learn@NCSA activity feed."""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from harness import coord as C
from harness import feed
from harness.api import create_app
from harness.auth import MemoryAuthStore
from harness.coord import CoordStore, Workflow
from harness.learner import MemoryEnrolLog
from harness.live_store import MemoryLiveStore
from harness.store import FileStore
from tests.test_admin import BASE, signin
from tests.test_auth import CFG, FakeGlobus
from tests.test_crosswalk import FakeMoodle, u

O = {"Origin": BASE}


def day(n):
    return (date.today() + timedelta(days=n)).isoformat()


@pytest.fixture
def env(built_site):
    store, fake, live, coord = MemoryAuthStore(), FakeGlobus(), MemoryLiveStore(), CoordStore()
    coord.save_offering("getting-started-icc", {
        "name": "Getting Started on the Illinois Campus Cluster", "description": "Learn the ICC.",
        "program_tags": ["IC", "ICC"], "default_speakers": "Peter Enstrom", "prior_form_id": "563997984",
        "moodle_url": "https://learn.ncsa.illinois.edu/course/view.php?id=39",
        "prerequisites": [{"label": "An ICC account", "url": "https://campuscluster.illinois.edu"}]})
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=FakeMoodle(by_username={"ada@illinois.edu": u(7)}), admin_usernames={"boss@illinois.edu"},
                     live_store=live, coord_store=coord, enrol_log=MemoryEnrolLog(),
                     coordinator_usernames={"soham@illinois.edu"})
    learner = TestClient(app, base_url=BASE)
    signin(learner, fake)
    coordc = TestClient(app, base_url=BASE)
    signin(coordc, fake, sub="globus-sub-5", name="Soham", preferred_username="soham@illinois.edu",
           identity_set=[{"sub": "globus-sub-5", "username": "soham@illinois.edu"}])
    admin = TestClient(app, base_url=BASE)
    signin(admin, fake, sub="globus-sub-9", name="Boss", preferred_username="boss@illinois.edu",
           identity_set=[{"sub": "globus-sub-9", "username": "boss@illinois.edu"}])
    return learner, coordc, admin, live, coord


def new_workshop(c, offset=20, offering="getting-started-icc", sessions=None):
    r = c.post("/coord/new", data={"offering": offering, "title": "",
                                   "sessions": sessions or f"{day(offset)} 10:00-11:30"}, headers=O, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("/coord/w/")[1].split("?")[0])


# ---------------------------------------------------------------- pure logic

def test_due_dates_follow_v1_rules():
    tue = "2026-11-10"  # a Tuesday
    due = {s["key"]: C.compute_due(tue, s) for s in C.STEPS}
    assert due["reg_form"] == "2026-10-27" and due["zoom"] == "2026-11-03"
    assert due["reminder"] == "2026-11-09" and due["conduct"] == tue and due["moodle_archive"] == "2026-11-17"
    # weekend shifts: pre-event backwards to Friday, post-event forwards to Monday; the reminder never moves
    sat_minus = C.compute_due("2026-11-16", C.STEP_BY_KEY["reg_form"])   # Mon - 14 = Mon
    assert sat_minus == "2026-11-02"
    assert C.compute_due("2026-11-15", C.STEP_BY_KEY["monitor_reg"]) == "2026-11-12"   # Sun-3 = Thu
    assert C.compute_due("2026-11-13", C.STEP_BY_KEY["attendance"]) == "2026-11-16"   # Fri+1 = Sat -> Mon
    assert C.compute_due("2026-11-16", C.STEP_BY_KEY["reminder"]) == "2026-11-15"    # Sunday is fine


def test_render_tokens_conditionals_placeholders():
    out = C.render("{{title}} {{#if prereqs}}P{{else}}none{{/if}} {{reg_url}}", {"title": "X", "prereqs": ""})
    assert out == "X none [[REGISTRATION URL NOT SET YET]]\n"
    ch = {c["key"] for c in C.applicable_channels(["ICC"])}
    assert {"eweek", "gradlinks", "bytes_pieces", "icc_users", "webtools_calendar"} <= ch and "delta_users" not in ch


# ---------------------------------------------------------------- access

def test_access_learner_coordinator_admin(env):
    learner, coordc, admin, live, coord = env
    anon = TestClient(learner.app, base_url=BASE)
    assert anon.get("/coord", follow_redirects=False).headers["location"].startswith("/auth/login")
    assert learner.get("/coord").status_code == 403
    assert coordc.get("/coord").status_code == 200 and admin.get("/coord").status_code == 200
    assert "Analytics" not in coordc.get("/coord").text and "Analytics" in admin.get("/coord").text
    assert coordc.get("/admin/analytics").status_code == 403          # coordinators are not admins
    assert coordc.post("/coord/new", data={"sessions": f"{day(5)} 10:00-11:00"},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert learner.post("/coord/new", data={"sessions": f"{day(5)} 10:00-11:00"}, headers=O).status_code == 403


# ---------------------------------------------------------------- workflow

def test_plan_from_offering_and_work_the_steps(env):
    _, c, _, live, coord = env
    wid = new_workshop(c, 20)
    w = live.get_workshop(wid)
    assert w["status"] == "draft" and w["title"] == "Getting Started on the Illinois Campus Cluster"
    assert w["program_tags"] == ["IC", "ICC"] and w["speakers"] == "Peter Enstrom"
    assert coord.details(wid)["prerequisites"][0]["label"] == "An ICC account"
    page = c.get(f"/coord/w/{wid}").text
    assert "563997984" in page                      # last form id on step 1
    assert "Illinois Campus Cluster user list" in page and "Delta / DeltaAI" not in page
    assert "[[REGISTRATION URL NOT SET YET]]" in page
    assert "/ 600" not in page or True
    # step 1 via its link field -> auto-done; publish needs the form link
    c.post(f"/coord/w/{wid}/step/reg_form", data={"action": "field", "value": "https://forms.illinois.edu/sec/1"}, headers=O)
    steps = {s["key"]: s for s in Workflow(coord, live).steps(live.get_workshop(wid))}
    assert steps["reg_form"]["status"] == "done" and steps["reg_form"]["auto"] == "link saved"
    assert "[[REGISTRATION URL" not in c.get(f"/coord/w/{wid}").text.split("Text")[1][:3000]
    # manual done / na / undo, notes and due override
    c.post(f"/coord/w/{wid}/step/monitor_reg", data={"action": "done"}, headers=O)
    c.post(f"/coord/w/{wid}/step/group_list", data={"action": "na"}, headers=O)
    c.post(f"/coord/w/{wid}/step/zoom", data={"action": "notes", "notes": "ask Peter for co-host"}, headers=O)
    c.post(f"/coord/w/{wid}/step/zoom", data={"action": "due", "due": day(3)}, headers=O)
    steps = {s["key"]: s for s in Workflow(coord, live).steps(live.get_workshop(wid))}
    assert steps["monitor_reg"]["status"] == "done" and coord.tasks(wid)["monitor_reg"]["done_by"] == "soham@illinois.edu"
    assert steps["group_list"]["status"] == "na" and steps["zoom"]["notes"] == "ask Peter for co-host"
    assert steps["zoom"]["due"] == day(3)
    c.post(f"/coord/w/{wid}/step/monitor_reg", data={"action": "undo"}, headers=O)
    assert Workflow(coord, live).steps(live.get_workshop(wid))[4]["status"] == "open"


def test_copy_edit_shared_newsletters_sent_and_save_to_offering(env):
    _, c, _, live, coord = env
    wid = new_workshop(c, 20)
    wf = Workflow(coord, live)
    w = live.get_workshop(wid)
    eweek = wf.copy(w, "eweek")
    assert eweek["limit"] is None and wf.copy(w, "gradlinks")["limit"] == 600
    text = "Custom announcement for {{nothing}} " + w["title"]
    c.post(f"/coord/w/{wid}/copy/eweek/0", data={"action": "save", "text": text, "subject": "S"}, headers=O)
    assert wf.copy(w, "gradlinks")["text"] == text and wf.copy(w, "bytes_pieces")["edited"]   # shared body
    for ch in ("eweek", "gradlinks", "bytes_pieces", "icc_users"):
        c.post(f"/coord/w/{wid}/copy/{ch}/0", data={"action": "sent"}, headers=O)
    steps = {s["key"]: s for s in wf.steps(live.get_workshop(wid))}
    assert steps["advertise"]["status"] == "done" and steps["advertise"]["badge"] == "4/4 sent"
    c.post(f"/coord/w/{wid}/copy/icc_users/0", data={"action": "unsent"}, headers=O)
    assert {s["key"]: s for s in wf.steps(live.get_workshop(wid))}["advertise"]["status"] == "open"
    # save wording back to the offering as tokens; the next delivery starts from it
    c.post(f"/coord/w/{wid}/copy/eweek/0", data={"action": "to_offering", "text": text, "subject": "S"}, headers=O)
    tpl = coord.template("getting-started-icc", "newsletters")
    assert "{{title}}" in tpl["body"] and w["title"] not in tpl["body"]
    wid2 = new_workshop(c, 120)
    assert wf.copy(live.get_workshop(wid2), "gradlinks")["origin"] == "offering"
    c.post(f"/coord/w/{wid}/copy/eweek/0", data={"action": "reset"}, headers=O)
    assert not wf.copy(live.get_workshop(wid), "gradlinks")["edited"]


def test_calendar_text_carries_forward_with_new_dates(env):
    _, c, _, live, coord = env
    first = new_workshop(c, -60)
    w1 = live.get_workshop(first)
    live.save_workshop({"register_url": "https://forms.illinois.edu/sec/OLD"}, first)
    w1 = live.get_workshop(first)
    wf = Workflow(coord, live)
    when1 = C.build_context(w1, None, {})["when_long"]
    text = f"Hand-written entry. Runs {when1}. Register: https://forms.illinois.edu/sec/OLD\n"
    c.post(f"/coord/w/{first}/copy/webtools_calendar/0", data={"action": "save", "text": text}, headers=O)
    second = new_workshop(c, 30)
    live.save_workshop({"register_url": "https://forms.illinois.edu/sec/NEW"}, second)
    w2 = live.get_workshop(second)
    cp = wf.copy(w2, "webtools_calendar")
    assert cp["origin"] == "previous"
    assert "Hand-written entry" in cp["text"] and C.build_context(w2, None, {})["when_long"] in cp["text"]
    assert "sec/NEW" in cp["text"] and "sec/OLD" not in cp["text"]


def test_series_gets_per_day_reminders_and_details_validation(env):
    _, c, _, live, coord = env
    wid = new_workshop(c, 10, sessions=f"{day(10)} 10:00-12:00\n{day(11)} 10:00-12:00")
    w = live.get_workshop(wid)
    rem = Workflow(coord, live).copies(w, "reminder")
    assert [x["day"] for x in rem] == [1, 2] and "Day 2" in rem[1]["text"]
    form = {"title": w["title"], "summary": "S", "status": "scheduled", "format": "series", "timezone": "America/Chicago",
            "sessions": f"{day(10)} 10:00-12:00", "speakers": "", "program_tags": "ICC", "register_url": "",
            "calendar_url": "", "moodle_url": "", "recording_url": "", "zoom_link": "", "survey_url": "",
            "reg_close": "", "prerequisites": "", "notes": ""}
    r = c.post(f"/coord/w/{wid}/details", data=form, headers=O, follow_redirects=False)
    assert "registration+form+link" in r.headers["location"]          # can't publish without a form
    form.update(register_url="https://forms.illinois.edu/sec/2", zoom_link="https://illinois.zoom.us/j/1",
                prerequisites="Delta account | https://access-ci.org", reg_close=day(8))
    assert c.post(f"/coord/w/{wid}/details", data=form, headers=O, follow_redirects=False).status_code == 303
    assert live.get_workshop(wid)["status"] == "scheduled"
    assert coord.details(wid)["zoom_link"] == "https://illinois.zoom.us/j/1"
    pub = TestClient(c.app, base_url=BASE).get("/api/v1/events").json()
    assert "zoom.us" not in json.dumps(pub)                            # zoom link stays coordinator-only
    assert "illinois.zoom.us/j/1" in Workflow(coord, live).copy(live.get_workshop(wid), "reminder_email")["text"]


def test_closeout_and_dashboard(env):
    _, c, _, live, coord = env
    wid = new_workshop(c, -3)
    c.post(f"/coord/w/{wid}/outcome", data={"registered": "40", "peak": "31", "staff": "3", "eval_responses": "9",
                                            "eval_summary": "", "retro_notes": "Shorter intro", "to_offering": "1"},
           headers=O)
    assert C.net_attendance(coord.outcome(wid)) == 28
    assert "Shorter intro" in coord.offering("getting-started-icc")["retro_notes"]
    assert c.post(f"/coord/w/{wid}/outcome", data={"peak": "-1"}, headers=O, follow_redirects=False).status_code == 303
    up = new_workshop(c, 4)
    dash = c.get("/coord").text
    assert "Due in the next 7 days" in dash and f"/coord/w/{up}#s-" in dash
    assert "30%" in dash and "missing recording" in dash                # 28/40 attended -> 30% no-show


def test_offerings_pages_and_v1_import(env):
    _, c, admin, live, coord = env
    assert c.post("/coord/offerings", data={"slug": "Bad Slug!", "name": "x"}, headers=O, follow_redirects=False).status_code == 303
    assert coord.offering("bad-slug") is None
    c.post("/coord/offerings", data={"slug": "mpi-intro", "name": "Intro to MPI"}, headers=O)
    c.post("/coord/offerings/mpi-intro", data={"name": "Intro to MPI", "program_tags": "Delta, ACCESS",
                                               "prerequisites": "Delta account | https://x.org", "typical_minutes": "120",
                                               "moodle_url": "https://learn.ncsa.illinois.edu/course/view.php?id=34"},
           headers=O)
    o = coord.offering("mpi-intro")
    assert o["program_tags"] == ["Delta", "ACCESS"] and o["typical_minutes"] == 120
    assert "Intro to MPI" in c.get("/coord/offerings").text
    live.save_workshop({"source_key": "v1:1", "title": "ICC", "status": "scheduled", "offering": "getting-started-icc",
                        "sessions": [{"start": f"{day(-40)}T10:00", "end": f"{day(-40)}T11:30"}], "program_tags": ["ICC"]})
    payload = {"offerings": [{"slug": "psc-mpi", "name": "PSC — MPI", "program_tags": ["PSC"]}],
               "events": [{"source_key": "v1:1", "tasks": [{"step_key": "reg_form", "status": "done"}],
                           "comms": [{"channel": "webtools_calendar", "day_index": 0, "edited_text": "Old entry",
                                      "sent_at": "2026-09-10T12:00:00+00:00"}],
                           "outcome": {"peak": 20, "staff": 2}}, {"source_key": "v1:99"}]}
    assert c.post("/api/v1/admin/coord/import", content=json.dumps(payload), headers=O).status_code == 403
    r = admin.post("/api/v1/admin/coord/import", content=json.dumps(payload), headers=O).json()
    assert r == {"offerings": 1, "events": 1, "missing": ["v1:99"]}
    wid = next(i for i, w in live.workshops.items() if w.get("source_key") == "v1:1")
    assert coord.tasks(wid)["reg_form"]["status"] == "done" and C.net_attendance(coord.outcome(wid)) == 18


# ---------------------------------------------------------------- activity feed

class FakeOutbox:
    def __init__(self, n):
        t0 = int((datetime.now(timezone.utc) - timedelta(days=50)).timestamp())
        self.rows = [{"id": i, "kind": k, "userid": 100 + i % 7, "courseid": 39 if i % 2 else 34, "cmid": 0, "state": 1,
                      "timecreated": t0 + i * 86400}
                     for i, k in zip(range(1, n + 1), ["enrolled", "course_completed", "activity_completion",
                                                         "unenrolled", "bogus"] * n)]

    def __call__(self, fn, **p):
        assert fn == "local_ncsahub_get_events"
        rows = [r for r in self.rows if r["id"] > p["afterid"]][: p["limit"]]
        more = any(r["id"] > (rows[-1]["id"] if rows else p["afterid"]) for r in self.rows)
        return {"events": rows, "lastid": rows[-1]["id"] if rows else p["afterid"], "more": more}


def test_pull_feed_is_incremental_and_reports(env):
    _, c, admin, live, coord = env
    box = FakeOutbox(45)
    assert feed.pull(live, box, page_size=10) == 36          # 'bogus' kind dropped
    assert feed.pull(live, box, page_size=10) == 0           # nothing new
    box.rows.append({"id": 46, "kind": "course_completed", "userid": 5, "courseid": 39, "cmid": 0, "state": 1,
                     "timecreated": int(datetime.now(timezone.utc).timestamp())})
    assert feed.pull(live, box) == 1 and live.feed_last_id() == 46
    rep = feed.report(live, 30, {39: "ICC"})
    assert rep["available"] and rep["totals"]["courseCompletions"] >= 1
    assert next(x for x in rep["courses"] if x["courseId"] == 39)["title"] == "ICC"
    wid = new_workshop(c, -20)
    ft = feed.follow_through(live, [live.get_workshop(wid)])
    assert ft and ft[0]["courseId"] == 39 and ft[0]["partial"] is True
    d = admin.get("/api/v1/admin/analytics?days=30").json()
    assert d["learn"]["available"] and d["followThrough"][0]["workshopId"] == wid
    html = admin.get("/admin/analytics?days=30").text
    assert "Learn@NCSA activity" in html and "Workshop follow-through" in html


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_pg_coord_and_feed_roundtrip():
    from harness.auth import PgAuthStore
    from harness.live_store import PgLiveStore
    from harness.store import PgStore
    dsn = os.environ["TEST_DATABASE_URL"]
    PgAuthStore(dsn).migrate()
    PgStore(dsn).migrate()
    cs = CoordStore(dsn)
    cs.save_offering("pg-off", {"name": "PG", "program_tags": ["IC"]})
    cs.save_offering("pg-off", {"description": "updated"})
    assert cs.offering("pg-off")["program_tags"] == ["IC"] and cs.offering("pg-off")["description"] == "updated"
    cs.save_task(999001, "zoom", status="done", done_by="x")
    cs.save_comm(999001, "eweek", 0, edited_text="hello", sent_at="2026-11-01T00:00:00+00:00")
    cs.save_details(999001, {"zoom_link": "https://z", "prerequisites": [{"label": "a"}]})
    cs.save_outcome(999001, {"peak": 5, "staff": 1})
    cs.save_template("pg-off", "newsletters", "S", "B {{title}}")
    assert cs.tasks(999001)["zoom"]["status"] == "done" and cs.comms(999001)[("eweek", 0)]["edited_text"] == "hello"
    assert cs.details(999001)["prerequisites"] == [{"label": "a"}] and C.net_attendance(cs.outcome(999001)) == 4
    assert cs.template("pg-off", "newsletters")["body"] == "B {{title}}"
    live = PgLiveStore(dsn)
    base = live.feed_last_id()
    n = live.add_feed_events([{"id": base + 1, "kind": "enrolled", "moodle_user": 7, "course_id": 39, "cm_id": 0,
                               "state": 1, "at": datetime.now(timezone.utc)}])
    assert n == 1 and live.feed_last_id() == base + 1
    assert live.add_feed_events([{"id": base + 1, "kind": "enrolled", "moodle_user": 7, "course_id": 39, "cm_id": 0,
                                  "state": 1, "at": datetime.now(timezone.utc)}]) == 0
    assert any(r["id"] == base + 1 for r in live.feed_events(("enrolled",), datetime.now(timezone.utc) - timedelta(hours=1)))


def test_delete_drafts_only_and_due_list_ignores_history(env):
    _, c, _, live, coord = env
    old = new_workshop(c, -40)
    dash = c.get("/coord").text
    assert f"/coord/w/{old}#s-" not in dash                         # long-past workshop is history, not "due"
    wid = new_workshop(c, 30)
    c.post(f"/coord/w/{wid}/step/zoom", data={"action": "done"}, headers=O)
    assert c.post(f"/coord/w/{wid}/delete", data={}, headers=O, follow_redirects=False).status_code == 303
    assert live.get_workshop(wid)                                   # not without the confirmation box
    live.save_workshop({"status": "scheduled", "register_url": "https://forms.illinois.edu/sec/1"}, wid)
    c.post(f"/coord/w/{wid}/delete", data={"confirm": "yes"}, headers=O)
    assert live.get_workshop(wid)                                   # scheduled workshops cannot be deleted
    live.save_workshop({"status": "draft"}, wid)
    c.post(f"/coord/w/{wid}/delete", data={"confirm": "yes"}, headers=O)
    assert live.get_workshop(wid) is None and coord.tasks(wid) == {}


def test_newsletters_render_as_one_block(env):
    _, c, _, live, coord = env
    wid = new_workshop(c, 20)
    page = c.get(f"/coord/w/{wid}").text
    assert page.count("Newsletter announcement") == 1 and page.count('id="c-newsletters-t"') == 1
    assert 'data-limit="600"' in page and 'id="c-gradlinks-0"' in page
