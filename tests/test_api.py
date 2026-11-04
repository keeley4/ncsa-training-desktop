"""The harness API returns exactly what the static snapshot publishes, and stubs later phases."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.contract import errors
from harness.store import FileStore


@pytest.fixture(scope="module")
def client(built_site):
    return TestClient(create_app(FileStore(built_site / "api" / "v1")))


@pytest.mark.parametrize("path,doc", [("/catalog/meta", "meta"), ("/categories", "categories"),
                                      ("/shelves", "shelves"), ("/tiles", "tiles")])
def test_parity_with_static(client, built_site, path, doc):
    r = client.get(f"/api/v1{path}")
    assert r.status_code == 200
    static = json.loads((built_site / "api" / "v1" / f"{path.lstrip('/')}.json").read_text())
    assert r.json() == static
    assert errors(doc, r.json()) == []


def test_single_tile_and_404(client):
    assert client.get("/api/v1/tiles/course-2").json()["title"] == "Intro to the Command Line"
    r = client.get("/api/v1/tiles/course-999")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


@pytest.mark.parametrize("method,path,phase", [("get", "/me", 3), ("post", "/enrollments", 3),
                                               ("get", "/events", 4), ("post", "/statements", 5)])
def test_later_phase_stubs(client, method, path, phase):
    r = getattr(client, method)(f"/api/v1{path}")
    assert r.status_code == 501 and r.json()["phase"] == phase


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_postgres_store_round_trip(result):
    from harness.store import PgStore
    store = PgStore(os.environ["TEST_DATABASE_URL"])
    store.migrate()
    store.save(result.documents())
    store.save(result.documents())          # second sync replaces, does not duplicate
    pg = TestClient(create_app(store))
    for path, doc in (("/catalog/meta", result.meta), ("/categories", result.categories),
                      ("/shelves", result.shelves), ("/tiles", result.tiles)):
        assert pg.get(f"/api/v1{path}").json() == doc
