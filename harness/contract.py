"""Validate documents against contract/openapi.yaml (OpenAPI 3.1 schemas are JSON Schema 2020-12)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

CONTRACT = Path(__file__).resolve().parent.parent / "contract" / "openapi.yaml"

# document name -> (path, method)
DOCUMENT_PATHS = {
    "meta": "/catalog/meta",
    "categories": "/categories",
    "shelves": "/shelves",
    "tiles": "/tiles",
    "tile": "/tiles/{tileId}",
}


@lru_cache(maxsize=1)
def spec() -> dict[str, Any]:
    with open(CONTRACT, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def response_schema(path: str, status: str = "200") -> dict[str, Any]:
    return spec()["paths"][path]["get"]["responses"][status]["content"]["application/json"]["schema"]


def validator_for(schema: dict[str, Any]) -> Draft202012Validator:
    # Root the schema in the whole spec so "#/components/..." refs resolve.
    root = {**spec(), **schema}
    return Draft202012Validator(root, format_checker=FormatChecker())


def errors(document: str, body: Any) -> list[str]:
    v = validator_for(response_schema(DOCUMENT_PATHS[document]))
    return [f"{document}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(body)]


def validate_documents(docs: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name in ("meta", "categories", "shelves", "tiles"):
        problems += errors(name, docs[name])
    return problems


def unresolved_refs() -> list[str]:
    """Every $ref in the contract must point at something."""
    missing: list[str] = []
    root = spec()

    def resolve(ref: str) -> bool:
        node: Any = root
        for part in ref.lstrip("#/").split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not resolve(ref):
                missing.append(ref)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(root)
    return missing
