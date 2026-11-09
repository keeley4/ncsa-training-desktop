"""Live workshops (public API, .ics, admin editor, import) and aggregate usage analytics."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.auth import MemoryAuthStore
from harness.contract import validator_for
from harness.contract import spec
from harness.learner import MemoryEnrolLog
from harness.live_store import MemoryLiveStore, import_v1_events
from harness.analytics import clean_events
from harness.store import FileStore
from harness.workshops import ics_document, parse_sessions
from tests.test_admin import BASE, signin
from tests.test_auth import CFG, FakeGlobus
from tests.test_crosswalk import FakeMoodle, u

SID = "0123456789abcdef0123"


def day(offset):
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


def doc(title, offset, status="scheduled", **kw):
    return {"title": title, "status": status, "register_url": "https://forms.illinois.edu/sec/1",
            "sessions": [{"start": f"{day(offset)}T10:00", "end": f"{day(offset)}T11:30"}], **kw}


@pytest.fixture
def env(built_site):
    store, fake, live = MemoryAuthStore(), FakeGlobus(), MemoryLiveStore()
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=FakeMoodle(by_username={"ada@illinois.edu": u(7)}),
                     admin_usernames={"boss@illinois.edu"}, live_store=live, enrol_log=MemoryEnrolLog())
    learner = TestClient(app, base_url=BASE)
    signin(learner, fake)
    admin = TestClient(app, base_url=BASE)
    signin(admin, fake, sub="globus-sub-9", name="Boss", preferred_username="boss@illinois.edu",
           identity_set=[{"sub": "globus-sub-9", "username": "boss@illinois.edu"}])
    return learner, admin, live


def schema(name):
    return validator_for({"$ref": f"#/components/schemas/{name}"})


# ---------------------------------------------------------------- workshops

def test_public_listing_hides_drafts_and_orders(env):
    learner, _, live = env
    live.save_workshop(doc("Later", 20))
    live.save_workshop(doc("Soon", 3))
    live.save_workshop(doc("Secret draft", 5, status="draft"))
    live.save_workshop(doc("Last month", -30))
    live.save_workshop(doc("Called off", 8, status="cancelled"))
    anon = TestClient(learner.app, base_url=BASE)
    up = anon.get("/api/v1/events").json()
    assert [w["title"] for w in up["items"]] == ["Soon", "Called off", "Later"]
    assert list(schema("WorkshopList").iter_errors(up)) == []
    assert up["items"][0]["sessions"][0]["start"].endswith(("-05:00", "-06:00"))
    assert [w["title"] for w in anon.get("/api/v1/events?when=past").json()["items"]] == ["Last month"]
    assert anon.get("/api/v1/events?when=bogus").status_code == 400
    draft_id = next(i for i, w in live.workshops.items() if w["title"] == "Secret draft")
    assert anon.get(f"/api/v1/events/{draft_id}").status_code == 404
    assert anon.get(f"/api/v1/events/{draft_id}.ics").status_code == 404


def test_ics_is_valid_and_updates_carry_sequence(env):
    learner, _, live = env
    wid = live.save_workshop({**doc("Intro to Parallel Computing, part 1; MPI", 4), "summary": "Line one\nline two",
                              "sessions": [{"start": f"{day(4)}T10:00", "end": f"{day(4)}T11:30"},
                                           {"start": f"{day(5)}T10:00", "end": f"{day(5)}T11:30"}]})
    r = learner.get(f"/api/v1/events/{wid}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "attachment" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 2 and f"UID:workshop-{wid}-2@" in text
    assert "SUMMARY:Intro to Parallel Computing\\, part 1\\; MPI (session 1 of 2)" in text
    assert "Line one\\nline two" in text and "SEQUENCE:0" in text and "STATUS:CONFIRMED" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    start = datetime.strptime(day(4), "%Y-%m-%d")
    assert f"DTSTART:{start:%Y%m%d}T1" in text  # 10:00 Chicago = 15:00 or 16:00 UTC
    live.save_workshop({"status": "cancelled"}, wid)
    text = learner.get(f"/api/v1/events/{wid}.ics").text
    assert "SEQUENCE:1" in text and "STATUS:CANCELLED" in text
    feed = learner.get("/api/v1/events.ics").text
    assert f"UID:workshop-{wid}-1@" in feed


def test_parse_sessions():
    assert parse_sessions("2026-11-10 9:00-11:30\n\n2026-11-09 13:00 – 15:00") == [
        {"start": "2026-11-09T13:00", "end": "2026-11-09T15:00"}, {"start": "2026-11-10T09:00", "end": "2026-11-10T11:30"}]
    for bad in ("", "Nov 10 10-11", "2026-11-10 11:00-10:00", "2026-02-30 10:00-11:00"):
        with pytest.raises(ValueError):
            parse_sessions(bad)


def test_admin_editor_is_admin_only_and_saves(env):
    learner, admin, live = env
    anon = TestClient(learner.app, base_url=BASE)
    assert anon.get("/admin/workshops", follow_redirects=False).headers["location"].startswith("/auth/login")
    assert learner.get("/admin/workshops").status_code == 403
    form = {"title": "GPUs on the ICC", "summary": "<b>bold</b>", "status": "scheduled", "format": "webinar",
            "timezone": "America/Chicago", "sessions": f"{day(9)} 10:00-11:30", "speakers": "Soham Pal",
            "register_url": "https://forms.illinois.edu/sec/9", "program_tags": "IC, ICC", "offering": "gpus-on-icc",
            "calendar_url": "", "moodle_url": "", "recording_url": ""}
    assert learner.post("/admin/workshops", data=form, headers={"Origin": BASE}).status_code == 403
    assert admin.post("/admin/workshops", data=form, headers={"Origin": "https://evil.example"}).status_code == 403
    r = admin.post("/admin/workshops", data=form, headers={"Origin": BASE}, follow_redirects=False)
    assert r.status_code == 303
    (w,) = live.workshops.values()
    assert w["program_tags"] == ["IC", "ICC"] and w["sessions"][0]["start"].endswith("T10:00")
    page = admin.get("/admin/workshops").text
    assert "GPUs on the ICC" in page and "<b>bold</b>" not in admin.get(f"/admin/workshops?edit={w['id']}").text
    bad = admin.post(f"/admin/workshops/{w['id']}", data={**form, "register_url": "http://insecure"}, headers={"Origin": BASE})
    assert bad.status_code == 422 and "https://" in bad.text and "10:00-11:30" in bad.text
    nolink = admin.post(f"/admin/workshops/{w['id']}", data={**form, "register_url": ""}, headers={"Origin": BASE})
    assert nolink.status_code == 422
    admin.post(f"/admin/workshops/{w['id']}", data={**form, "status": "cancelled"}, headers={"Origin": BASE})
    assert live.workshops[w["id"]]["status"] == "cancelled" and live.workshops[w["id"]]["sequence"] == 1


def test_v1_import_is_idempotent_and_public_fields_only(env):
    _, admin, live = env
    rows = [{"id": 5, "title": "Intro to Parallel Computing", "start_date": day(1), "start_time": "10:00",
             "end_time": "11:30", "status": "tentative", "speakers": "Soham Pal", "program_tags": '["IC"]',
             "slug": "intro-parallel-computing", "zoom_link": "https://zoom.us/j/SECRET", "reg_form_url": "",
             "moodle_url": "https://learn.ncsa.illinois.edu/course/view.php?id=101", "days": [{"date": day(1)}]},
            {"id": 2, "title": "Getting Started with DeltaAI", "start_date": day(-20), "start_time": "13:00",
             "end_time": "15:00", "status": "confirmed", "reg_form_url": "https://forms.uofi.uillinois.edu/sec/8",
             "moodle_url": "https://learn.ncsa.illinois.edu/", "notes_public": "Learn DeltaAI.", "program_tags": "[]"}]
    docs = import_v1_events(rows, {"intro-parallel-computing": {"description": "What parallel means."}})
    assert "SECRET" not in json.dumps(docs)
    assert [d["status"] for d in docs] == ["draft", "scheduled"] and docs[1]["moodle_url"] == ""
    for _ in range(2):
        r = admin.post("/api/v1/admin/workshops/import", content=json.dumps(docs), headers={"Origin": BASE})
        assert r.status_code == 200, r.text
    assert len(live.workshops) == 2
    assert live.workshops[1]["summary"] == "What parallel means."
    assert admin.post("/api/v1/admin/workshops/import", content=b"[{}]", headers={"Origin": BASE}).status_code == 207


# ---------------------------------------------------------------- analytics

def beacon(c, events, sid=SID, origin=BASE):
    return c.post("/api/v1/usage", content=json.dumps({"sid": sid, "events": events}),
                  headers={"Origin": origin, "Content-Type": "application/json"})


def test_beacon_accepts_known_kinds_only_and_stores_no_identity(env):
    learner, _, live = env
    anon = TestClient(learner.app, base_url=BASE)
    assert beacon(anon, [{"kind": "visit", "target": "home"}, {"kind": "search", "detail": "  MPI   Basics ", "n": 0},
                         {"kind": "signin"}, {"kind": "drop_table"}]).status_code == 204
    assert beacon(learner, [{"kind": "tile_open", "target": "course-2"}]).status_code == 204
    assert beacon(anon, [{"kind": "visit"}], origin="https://evil.example").status_code == 204   # ignored
    assert beacon(anon, [{"kind": "visit"}], sid="not-hex").status_code == 400
    assert beacon(anon, [{"kind": "visit"}] * 30).status_code == 400
    kinds = [(e["kind"], e["signed_in"]) for e in live.usage]
    assert ("search", False) in kinds and ("tile_open", True) in kinds and ("signin", True) in kinds  # signin from login
    assert all(set(e) <= {"at", "sid", "signed_in", "kind", "target", "detail", "n"} for e in live.usage)
    assert next(e for e in live.usage if e["kind"] == "search")["detail"] == "mpi basics"
    assert sum(1 for e in live.usage if e["kind"] == "visit") == 1


def test_clean_events_limits():
    out = clean_events({"sid": SID, "events": [{"kind": "launch", "target": "x" * 500, "n": -5}]}, False)
    assert len(out[0]["target"]) == 80 and out[0]["n"] is None


def test_dashboard_admin_only_and_reports(env):
    learner, admin, live = env
    anon = TestClient(learner.app, base_url=BASE)
    wid = live.save_workshop(doc("Delta 101", 3))
    beacon(anon, [{"kind": "visit"}, {"kind": "search", "detail": "gpu", "n": 3}, {"kind": "search", "detail": "zzz", "n": 0},
                  {"kind": "tile_open", "target": "course-2"}, {"kind": "launch", "target": "course-2", "detail": "open"},
                  {"kind": "register_click", "target": str(wid)}, {"kind": "ics_download", "target": str(wid)}])
    beacon(learner, [{"kind": "visit"}], sid="ffffffffffffffff")
    live.set_course_stats({2: 41})
    assert anon.get("/api/v1/admin/analytics").status_code == 401
    assert learner.get("/api/v1/admin/analytics").status_code == 403
    assert learner.get("/admin/analytics").status_code == 403
    d = admin.get("/api/v1/admin/analytics?days=7").json()
    assert d["totals"]["sessions"] == 2 and d["totals"]["signedInSessions"] == 1
    assert d["totals"]["signin"] == 2 and d["totals"]["search"] == 2
    assert d["zeroResultSearches"] == [{"term": "zzz", "count": 1}]
    assert d["tiles"][0] == {"tileId": "course-2", "opens": 1, "launches": 1}
    assert d["workshopActions"][str(wid)] == {"register_click": 1, "ics_download": 1}
    assert d["workshopTitles"][str(wid)] == "Delta 101"
    assert d["accounts"] == {"linked": 1, "none": 1}
    assert next(c for c in d["courses"] if c["courseId"] == 2)["enrolled"] == 41
    assert len(d["daily"]) == 7 and d["daily"][-1]["visits"] == 2
    html = admin.get("/admin/analytics?days=7").text
    assert "Usage analytics" in html and "zzz" in html and "Delta 101" in html and "Intro to the Command Line" in html
    assert "ada@illinois.edu" not in html


def test_front_door_ships_workshops_and_beacon():
    web = (os.path.dirname(__file__) + "/../web/app.js")
    js = open(web).read()
    assert "/api/v1/usage" in js and "#/workshops" in js and "sendBeacon" in js
    assert "localStorage" not in js.split("usage analytics")[1].split("live workshops")[0]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_pg_live_store_roundtrip():
    from harness.auth import PgAuthStore
    from harness.live_store import PgLiveStore, retention_cutoff
    dsn = os.environ["TEST_DATABASE_URL"]
    PgAuthStore(dsn).migrate()
    live = PgLiveStore(dsn)
    live.migrate()
    wid = live.save_workshop({**doc("PG workshop", 2), "source_key": "test:pg", "program_tags": ["IC"], "summary": "",
                              "speakers": "", "format": "webinar", "timezone": "America/Chicago", "offering": "",
                              "calendar_url": "", "moodle_url": "", "recording_url": ""})
    before = live.get_workshop(wid)["sequence"]
    assert live.save_workshop({"source_key": "test:pg", "title": "PG workshop v2"}) == wid
    w = live.get_workshop(wid)
    assert w["title"] == "PG workshop v2" and w["sequence"] == before + 1 and w["program_tags"] == ["IC"]
    live.add_usage([{"sid": SID, "signed_in": False, "kind": "visit"}])
    assert any(r["kind"] == "visit" for r in live.usage_since(datetime.now(timezone.utc) - timedelta(minutes=5)))
    live.set_course_stats({2: 5})
    live.set_course_stats({2: 6})
    assert live.course_stats()[2]["enrolled"] == 6
    assert live.purge_usage(retention_cutoff()) >= 0
    assert "test:pg" in json.dumps(live.list_workshops())
