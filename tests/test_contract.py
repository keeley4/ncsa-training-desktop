"""The contract is self-consistent and every published document matches it."""
import json

from harness.contract import errors, unresolved_refs, validate_documents, spec


def test_contract_refs_resolve():
    assert unresolved_refs() == []


def test_every_operation_has_phase_and_id():
    for path, ops in spec()["paths"].items():
        for method, op in ops.items():
            assert "operationId" in op, f"{method} {path}"
            assert isinstance(op.get("x-phase"), int), f"{method} {path}"


def test_fixture_documents_valid(result):
    assert validate_documents(result.documents()) == []


def test_single_tile_files_valid(built_site):
    files = sorted((built_site / "api" / "v1" / "tiles").glob("*.json"))
    assert files
    for f in files:
        assert errors("tile", json.loads(f.read_text())) == []


def test_validator_catches_bad_documents(result):
    bad = json.loads(json.dumps(result.tiles))
    bad["items"][0]["type"] = "spaceship"
    bad["items"][0]["surprise"] = 1
    problems = errors("tiles", bad)
    assert any("spaceship" in p for p in problems)
    assert any("surprise" in p for p in problems)
