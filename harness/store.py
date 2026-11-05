"""Where the harness API reads the catalog from.

FileStore reads the same JSON files that are published to GitHub Pages.
PgStore keeps the catalog in Postgres (local Docker now, the NCSA host from Phase 3).
Both return documents that match the contract, so the API never cares which one it has.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

SCHEMA_SQL = Path(__file__).resolve().parent / "db" / "schema.sql"


class Store(Protocol):
    def meta(self) -> dict[str, Any] | None: ...
    def categories(self) -> dict[str, Any]: ...
    def shelves(self) -> dict[str, Any]: ...
    def tiles(self) -> dict[str, Any]: ...
    def tile(self, tile_id: str) -> dict[str, Any] | None: ...


class FileStore:
    """Reads <root>/catalog/meta.json, categories.json, shelves.json, tiles.json."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _read(self, rel: str) -> Any:
        p = self.root / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def meta(self) -> dict[str, Any] | None:
        return self._read("catalog/meta.json")

    def categories(self) -> dict[str, Any]:
        return self._read("categories.json") or {"items": []}

    def shelves(self) -> dict[str, Any]:
        return self._read("shelves.json") or {"items": []}

    def tiles(self) -> dict[str, Any]:
        return self._read("tiles.json") or {"items": []}

    def tile(self, tile_id: str) -> dict[str, Any] | None:
        return next((t for t in self.tiles()["items"] if t["id"] == tile_id), None)


class PgStore:
    def __init__(self, dsn: str):
        import psycopg  # imported lazily so file-only use needs no driver

        self._psycopg = psycopg
        self.dsn = dsn

    def _conn(self):
        return self._psycopg.connect(self.dsn, autocommit=False)

    def migrate(self) -> None:
        from .auth import SCHEMA_SQL as AUTH_SQL

        with self._conn() as conn:
            conn.execute(SCHEMA_SQL.read_text())
            conn.execute(AUTH_SQL.read_text())
            conn.commit()

    def save(self, docs: dict[str, Any]) -> int:
        """Replace the catalog atomically. Returns the sync run id."""
        from psycopg.types.json import Jsonb

        meta = docs["meta"]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sync_runs (mode, status, meta) VALUES (%s, 'running', %s) RETURNING id",
                (meta["source"]["mode"], Jsonb(meta)),
            )
            run_id = cur.fetchone()[0]
            cur.execute("DELETE FROM shelf_tiles")
            cur.execute("DELETE FROM shelves")
            cur.execute("DELETE FROM tiles")
            cur.execute("DELETE FROM categories")
            for pos, c in enumerate(docs["categories"]["items"]):
                cur.execute(
                    "INSERT INTO categories (id, position, name, parent_id, course_count) VALUES (%s, %s, %s, %s, %s)",
                    (c["id"], pos, c["name"], c["parentId"], c["courseCount"]),
                )
            for pos, t in enumerate(docs["tiles"]["items"]):
                cur.execute(
                    "INSERT INTO tiles (id, position, type, title, doc) VALUES (%s, %s, %s, %s, %s)",
                    (t["id"], pos, t["type"], t["title"], Jsonb(t)),
                )
            for pos, s in enumerate(docs["shelves"]["items"]):
                cur.execute(
                    "INSERT INTO shelves (id, position, title, purpose, start_here) VALUES (%s, %s, %s, %s, %s)",
                    (s["id"], pos, s["title"], s["purpose"], s["startHere"]),
                )
                for tpos, tid in enumerate(s["tileIds"]):
                    cur.execute(
                        "INSERT INTO shelf_tiles (shelf_id, position, tile_id) VALUES (%s, %s, %s)",
                        (s["id"], tpos, tid),
                    )
            cur.execute("UPDATE sync_runs SET status='ok', finished_at=now() WHERE id=%s", (run_id,))
            conn.commit()
        return run_id

    def meta(self) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT meta FROM sync_runs WHERE status='ok' ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def categories(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id, name, parent_id, course_count FROM categories ORDER BY position").fetchall()
        return {"items": [{"id": r[0], "name": r[1], "parentId": r[2], "courseCount": r[3]} for r in rows]}

    def shelves(self) -> dict[str, Any]:
        with self._conn() as conn:
            shelves = conn.execute("SELECT id, title, purpose, start_here FROM shelves ORDER BY position").fetchall()
            links = conn.execute("SELECT shelf_id, tile_id FROM shelf_tiles ORDER BY shelf_id, position").fetchall()
        by_shelf: dict[str, list[str]] = {}
        for sid, tid in links:
            by_shelf.setdefault(sid, []).append(tid)
        return {
            "items": [
                {"id": s[0], "title": s[1], "purpose": s[2], "tileIds": by_shelf.get(s[0], []), "startHere": s[3]}
                for s in shelves
            ]
        }

    def tiles(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT doc FROM tiles ORDER BY position").fetchall()
        return {"items": [r[0] for r in rows]}

    def tile(self, tile_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT doc FROM tiles WHERE id=%s", (tile_id,)).fetchone()
        return row[0] if row else None
