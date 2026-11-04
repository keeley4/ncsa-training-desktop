import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HARNESS_NO_AUTOAPP", "1")


@pytest.fixture(scope="session")
def fixture_source():
    from harness.moodle import FixtureSource
    return FixtureSource(ROOT / "fixtures" / "moodle")


@pytest.fixture(scope="session")
def fixture_shelves():
    from harness.catalog import load_shelves
    return load_shelves(str(ROOT / "fixtures" / "shelves.yaml"))


@pytest.fixture(scope="session")
def result(fixture_source, fixture_shelves):
    from harness.catalog import build_catalog
    return build_catalog(fixture_source, fixture_shelves)


@pytest.fixture(scope="session")
def built_site(tmp_path_factory):
    """A full static build from fixtures, with a fake token in the environment."""
    from harness.__main__ import main
    out = tmp_path_factory.mktemp("site")
    os.environ["MOODLE_TOKEN"] = "canary-token-0123456789abcdef"
    try:
        main(["build", "--mode", "fixture", "--fixtures", str(ROOT / "fixtures" / "moodle"),
              "--shelves", str(ROOT / "fixtures" / "shelves.yaml"), "--out", str(out)])
    finally:
        os.environ.pop("MOODLE_TOKEN", None)
    return out
