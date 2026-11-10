"""Learning paths from From Laptop to Cluster: mapping, public endpoint, saved path, analytics counts."""
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from harness import paths as P
from harness.api import create_app
from harness.auth import MemoryAuthStore
from harness.live_store import MemoryLiveStore
from harness.store import FileStore
from tests.test_admin import BASE, signin
from tests.test_auth import CFG, FakeGlobus
from tests.test_crosswalk import FakeMoodle, u

O = {"Origin": BASE}
CONF = {
    "l2c_url": "https://example.org/l2c/",
    "stages": [
        {"id": "start", "title": "Start", "l2c_steps": [1, 2], "tiles": ["course:2"]},
        {"id": "cluster", "title": "Cluster", "l2c_steps": [4, 7], "tiles": ["course:3", "course:777"],
         "offerings": ["gs-icc"]},
        {"id": "scale", "title": "Scale up", "l2c_steps": [5, 8], "tiles": ["course:4"]},
    ],
    "answers": {
        "work": [{"id": "sim", "name": "One large simulation"}, {"id": "web", "name": "Host a website"}],
        "scale": [{"id": "S", "name": "Small"}, {"id": "L", "name": "Large"}],
        "fit": [{"id": "iccp", "name": "Campus Cluster"}, {"id": "cpanel", "name": "cPanel"}],
        "ex": [{"id": "climate", "name": "Weather and climate"}],
        "par": [{"id": "yes", "name": "Many cores"}, {"id": "no", "name": "One core"}],
    },
    "fit_stage": {"iccp": "cluster", "cpanel": None},
    "scale_up": {"iccp": {"scales": ["L"], "stage": "scale"}},
    "fit_tiles": {"iccp": ["course:3"]},
    "work_tiles": {"sim": ["course:4", "course:3"]},
    "par_tiles": {"no": ["course:2"]},
    "fit_offerings": {"iccp": ["gs-icc"]},
    "no_path_note": "Not covered yet.",
}
Q = {"work": "sim", "scale": "S", "fit": "iccp", "ex": "climate", "par": "no"}


def day(n):
    return (date.today() + timedelta(days=n)).isoformat()


@pytest.fixture
def env(built_site):
    store, fake, live = MemoryAuthStore(), FakeGlobus(), MemoryLiveStore()
    live.save_workshop({"title": "ICC webinar", "status": "scheduled", "offering": "gs-icc",
                        "sessions": [{"start": f"{day(5)}T10:00", "end": f"{day(5)}T11:30"}]})
    live.save_workshop({"title": "Draft", "status": "draft", "offering": "gs-icc",
                        "sessions": [{"start": f"{day(6)}T10:00", "end": f"{day(6)}T11:30"}]})
    live.save_workshop({"title": "Old", "status": "scheduled", "offering": "gs-icc",
                        "sessions": [{"start": f"{day(-6)}T10:00", "end": f"{day(-6)}T11:30"}]})
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake,
                     lookup=FakeMoodle(by_username={"ada@illinois.edu": u(7)}), admin_usernames={"boss@illinois.edu"},
                     live_store=live, paths_config=CONF)
    learner = TestClient(app, base_url=BASE)
    signin(learner, fake)
    admin = TestClient(app, base_url=BASE)
    signin(admin, fake, sub="globus-sub-9", name="Boss", preferred_username="boss@illinois.edu",
           identity_set=[{"sub": "globus-sub-9", "username": "boss@illinois.edu"}])
    return learner, admin, live, app


def test_build_path_stage_focus_and_workshops(env):
    *_, live, app = env
    cfg = P.load_config(CONF)
    titles = {"course-2": "A", "course-3": "B", "course-4": "C"}
    p = P.build_path(cfg, P.clean_answers(cfg, Q), titles, live.list_workshops())
    assert p["here"] == "cluster"
    assert [s["position"] for s in p["stages"]] == ["before", "here", "after"]
    assert p["focus"] == ["course-2", "course-3", "course-4"]           # fit, work, par candidates; deduped; journey order
    assert p["focusStage"] == {"course-2": "1", "course-3": "2", "course-4": "3"}
    cl = p["stages"][1]
    assert cl["tileIds"] == ["course-3"]                                  # course 777 is not on the front door
    assert [w["title"] for w in cl["workshops"]] == ["ICC webinar"]       # no drafts, nothing past
    assert [w["title"] for w in p["focusWorkshops"]] == ["ICC webinar"]
    big = P.build_path(cfg, P.clean_answers(cfg, {**Q, "scale": "L"}), titles, [])
    assert big["here"] == "scale"
    web = P.build_path(cfg, P.clean_answers(cfg, {"work": "web", "scale": "S", "fit": "cpanel"}), titles, [])
    assert web["here"] is None and web["focus"] == [] and web["note"] == "Not covered yet."


def test_answers_are_validated():
    cfg = P.load_config(CONF)
    with pytest.raises(ValueError):
        P.clean_answers(cfg, {"work": "sim", "scale": "S"})
    with pytest.raises(ValueError):
        P.clean_answers(cfg, {**Q, "ex": "<script>"})
    assert "par" not in P.clean_answers(cfg, {**Q, "par": ""})


def test_public_endpoint(env):
    learner, *_ = env
    anon = TestClient(learner.app, base_url=BASE)
    r = anon.get("/api/v1/paths", params=Q)
    assert r.status_code == 200 and r.json()["labels"]["work"] == "One large simulation"
    assert anon.get("/api/v1/paths", params={**Q, "fit": "mars"}).status_code == 400


def test_save_show_and_forget(env):
    learner, admin, live, app = env
    anon = TestClient(learner.app, base_url=BASE)
    assert anon.get("/api/v1/me/path").status_code == 401
    assert anon.put("/api/v1/me/path", json=Q, headers=O).status_code == 401
    assert learner.get("/api/v1/me/path").json() == {"saved": False}
    assert learner.put("/api/v1/me/path", json=Q, headers={"Origin": "https://evil.example"}).status_code == 403
    assert learner.put("/api/v1/me/path", json={**Q, "work": "nope"}, headers=O).status_code == 400
    r = learner.put("/api/v1/me/path", json={**Q, "name": "Ada"}, headers=O)   # extra keys are ignored
    assert r.status_code == 200 and r.json()["path"]["here"] == "cluster"
    row = app.state.path_store.all_paths()[0]
    assert set(row) <= set(P.ANSWER_KEYS) | {"saved_at", "updated_at"} and "Ada" not in str(row)
    mine = learner.get("/api/v1/me/path").json()
    assert mine["saved"] and mine["answers"] == Q
    assert admin.get("/api/v1/me/path").json() == {"saved": False}         # per account
    learner.put("/api/v1/me/path", json={**Q, "scale": "L"}, headers=O)    # replace, not add
    assert len(app.state.path_store.all_paths()) == 1
    rep = admin.get("/api/v1/admin/analytics?days=30").json()
    assert rep["paths"]["saved"] == 1 and rep["paths"]["scale"][0] == {"id": "L", "name": "Large", "count": 1}
    assert rep["totals"]["path_save"] == 2
    assert "Learning paths (From Laptop to Cluster)" in admin.get("/admin/analytics").text
    assert learner.delete("/api/v1/me/path", headers=O).json() == {"saved": False}
    assert learner.get("/api/v1/me/path").json() == {"saved": False}


def test_path_view_beacon_counts(env):
    learner, admin, live, _ = env
    sid = "a" * 24
    learner.post("/api/v1/usage", json={"sid": sid, "events": [
        {"kind": "path_view", "target": "l2c", "detail": "sim:S:iccp"},
        {"kind": "path_view", "target": "", "detail": "sim:S:iccp"},
        {"kind": "path_save", "target": "iccp"}]}, headers=O)            # path_save is server-side only
    rep = admin.get("/api/v1/admin/analytics?days=7").json()
    assert rep["pathViews"] == {"total": 2, "fromL2C": 1, "byFit": {"iccp": 2}}
    assert rep["totals"]["path_save"] == 0


def test_real_mapping_file_loads():
    cfg = P.load_config()
    ids = {s["id"] for s in cfg["stages"]}
    assert ids == {"start", "icrn", "cluster", "parallel", "systems"}
    for k in ("work", "scale", "fit", "ex", "par"):
        assert cfg["answers"][k]
    assert set(cfg["fit_stage"]) == set(cfg["answers"]["fit"])
    assert set(cfg["work_tiles"]) <= set(cfg["answers"]["work"])


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_pg_path_store_roundtrip():
    from harness.store import PgStore
    dsn = os.environ["TEST_DATABASE_URL"]
    PgStore(dsn).migrate()
    s = P.PgPathStore(dsn)
    s.save_path("pg-test-user", Q)
    s.save_path("pg-test-user", {**Q, "scale": "L"})
    assert s.get_path("pg-test-user")["scale"] == "L"
    assert any(r["fit"] == "iccp" for r in s.all_paths())
    assert s.delete_path("pg-test-user") and s.get_path("pg-test-user") is None


def test_general_journey_without_answers(env):
    learner, *_ = env
    anon = TestClient(learner.app, base_url=BASE)
    j = anon.get("/api/v1/paths").json()
    assert j["general"] is True and j["here"] is None and j["focus"] == [] and j["note"] == ""
    assert [s["position"] for s in j["stages"]] == ["none", "none", "none"]
    assert [w["title"] for w in j["stages"][1]["workshops"]] == ["ICC webinar"]
    assert anon.get("/api/v1/paths", params={"work": "sim"}).status_code == 400   # partial answers still rejected


def test_front_door_has_entry_points():
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "web"
    html, js = (web / "index.html").read_text(), (web / "app.js").read_text()
    assert 'id="nav-path"' in html and ">Learning path<" in html
    assert "journeyBanner" in js and "Learning path: laptop to cluster" in js and "stageOfTile" in js


def test_stage_steps_also_label_and_focus_order():
    conf = dict(CONF, focus_max=2, stages=[
        {"id": "start", "label": "1", "title": "Start", "steps": ["course:2"], "ready": "Can use a terminal."},
        {"id": "cluster", "title": "Cluster", "steps": ["course:3"], "also": ["course:4", "course:3"]},
    ], scale_up={}, fit_stage={"iccp": "cluster", "cpanel": None})
    cfg = P.load_config(conf)
    titles = {"course-2": "A", "course-3": "B", "course-4": "C"}
    p = P.build_path(cfg, {"work": "sim", "scale": "S", "fit": "iccp"}, titles, [])
    st = p["stages"][1]
    assert st["label"] == "2"                                   # default label = position
    assert st["stepIds"] == ["course-3"] and st["alsoIds"] == ["course-4"]   # a step is not repeated in also
    assert st["tileIds"] == ["course-3", "course-4"]
    assert p["stages"][0]["ready"] == "Can use a terminal."
    # candidates: fit course-3, work course-4 + course-3 -> course-3 (step) before course-4 (also); cut to 2
    assert p["focus"] == ["course-3", "course-4"]
    assert p["focusStage"] == {"course-3": "2", "course-4": "2"}


def test_real_paths_yaml_resolves_and_orders():
    cfg = P.load_config()
    seen = [t for s in cfg["stages"] for t in s["steps"]]
    assert len(seen) == len(set(seen)), "a course is a step on two stages"
    assert cfg["focus_max"] == 3
    titles = {t: t for s in cfg["stages"] for t in s["tiles"]}
    p = P.build_path(cfg, {"work": "sim", "scale": "S", "fit": "iccp", "par": "yes"}, titles, [])
    assert p["here"] == "cluster"
    assert p["focus"] == ["course-39", "course-72", "course-43"]
    assert [p["focusStage"][t] for t in p["focus"]] == ["3", "4a", "4a"]
