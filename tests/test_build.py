"""The static build: right files, no secrets, refuses bad input."""
import json
import os

import pytest

from harness.__main__ import main

from conftest import ROOT

FIXTURE_SHELVES = str(ROOT / "fixtures" / "shelves.yaml")


def test_static_layout(built_site):
    for rel in ("index.html", "app.js", "styles.css", "config.json", ".nojekyll",
                "api/v1/catalog/meta.json", "api/v1/shelves.json", "api/v1/tiles.json",
                "api/v1/categories.json", "api/v1/openapi.yaml", "api/v1/tiles/course-2.json"):
        assert (built_site / rel).exists(), rel
    cfg = json.loads((built_site / "config.json").read_text())
    assert cfg == {"apiBase": "api/v1", "suffix": ".json"}


def test_no_token_anywhere(built_site):
    for p in built_site.rglob("*"):
        if p.is_file():
            assert b"canary-token-0123456789abcdef" not in p.read_bytes(), p


def test_refuses_to_publish_a_secret(tmp_path, monkeypatch):
    # a token that happens to appear in the catalog text must stop the build
    monkeypatch.setenv("MOODLE_TOKEN", "Summer School 2025")
    with pytest.raises(SystemExit) as exc:
        main(["build", "--mode", "fixture", "--out", str(tmp_path / "o"),
              "--shelves", FIXTURE_SHELVES])
    assert "secret" in str(exc.value)


def test_fail_on_warnings(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["build", "--mode", "fixture", "--out", str(tmp_path / "o"),
              "--shelves", FIXTURE_SHELVES, "--fail-on-warnings"])
    assert "Warnings present" in str(exc.value)


def test_live_mode_needs_token(tmp_path, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN", raising=False)
    monkeypatch.delenv("MOODLE_TOKEN_FILE", raising=False)
    with pytest.raises(ValueError):
        main(["build", "--mode", "live", "--site", "https://moodle.example.org", "--out", str(tmp_path / "o")])


def test_real_shelves_file_parses():
    from harness.catalog import load_shelves, _ref_to_tile_id
    cfg = load_shelves(str(ROOT / "catalog" / "shelves.yaml"))
    refs = [r for s in cfg["shelves"] for r in s["tiles"]]
    assert len(cfg["shelves"]) == 8
    for r in refs:
        _ref_to_tile_id(r)
    # every Moodle course id appears once across shelves and groups, except deliberate repeats
    singles = [int(r.split(":")[1]) for r in refs if r.startswith("course:")]
    grouped = [c for g in cfg["groups"].values() for c in g["courses"]]
    assert len(set(singles) | set(grouped)) == 41
