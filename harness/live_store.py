"""Storage for live workshops, usage events and course stats (Memory for tests, Postgres on the host)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_SQL = Path(__file__).resolve().parent / "db" / "schema_live.sql"

FIELDS = ["source_key", "offering", "title", "summary", "program_tags", "speakers", "format", "timezone",
          "sessions", "status", "register_url", "calendar_url", "moodle_url", "recording_url"]
JSON_FIELDS = {"program_tags", "sessions"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_doc(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


class MemoryLiveStore:
    def __init__(self) -> None:
        self.workshops: dict[int, dict[str, Any]] = {}
        self.usage: list[dict[str, Any]] = []
        self.stats: dict[int, tuple[int, datetime]] = {}

    # workshops
    def list_workshops(self) -> list[dict[str, Any]]:
        return [dict(w) for w in self.workshops.values()]

    def get_workshop(self, wid: int) -> dict[str, Any] | None:
        w = self.workshops.get(wid)
        return dict(w) if w else None

    def save_workshop(self, doc: dict[str, Any], wid: int | None = None) -> int:
        if wid is None and doc.get("source_key"):
            wid = next((i for i, w in self.workshops.items() if w.get("source_key") == doc["source_key"]), None)
        now = _now().isoformat()
        if wid is None or wid not in self.workshops:
            wid = max(self.workshops, default=0) + 1
            self.workshops[wid] = {"id": wid, "sequence": 0, "created_at": now, **{f: doc.get(f) for f in FIELDS}}
        else:
            w = self.workshops[wid]
            w.update({f: doc[f] for f in FIELDS if f in doc})
            w["sequence"] += 1
        self.workshops[wid]["updated_at"] = now
        return wid

    # usage
    def add_usage(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self.usage.append({"at": r.get("at") or _now(), "sid": r.get("sid", ""), "signed_in": bool(r.get("signed_in")),
                               "kind": r["kind"], "target": r.get("target", ""), "detail": r.get("detail", ""),
                               "n": r.get("n")})

    def usage_since(self, since: datetime) -> list[dict[str, Any]]:
        return [u for u in self.usage if u["at"] >= since]

    def purge_usage(self, before: datetime) -> int:
        n = len(self.usage)
        self.usage = [u for u in self.usage if u["at"] >= before]
        return n - len(self.usage)

    # course stats
    def set_course_stats(self, counts: dict[int, int]) -> None:
        now = _now()
        for cid, n in counts.items():
            self.stats[cid] = (n, now)

    def course_stats(self) -> dict[int, dict[str, Any]]:
        return {cid: {"enrolled": n, "checkedAt": at.isoformat()} for cid, (n, at) in self.stats.items()}


class PgLiveStore:
    def __init__(self, dsn: str):
        import psycopg
        from psycopg.rows import dict_row

        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _conn(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(SCHEMA_SQL.read_text())
            conn.commit()

    def list_workshops(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM workshops ORDER BY id").fetchall()
        return [_row_doc(r) for r in rows]

    def get_workshop(self, wid: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM workshops WHERE id=%s", (wid,)).fetchone()
        return _row_doc(row) if row else None

    def save_workshop(self, doc: dict[str, Any], wid: int | None = None) -> int:
        from psycopg.types.json import Jsonb

        vals = {f: (Jsonb(doc[f]) if f in JSON_FIELDS else doc[f]) for f in FIELDS if f in doc}
        with self._conn() as conn:
            if wid is None and doc.get("source_key"):
                row = conn.execute("SELECT id FROM workshops WHERE source_key=%s", (doc["source_key"],)).fetchone()
                wid = row["id"] if row else None
            if wid is None:
                cols = list(vals)
                row = conn.execute(
                    f"INSERT INTO workshops ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                    [vals[c] for c in cols]).fetchone()
                wid = row["id"]
            else:
                sets = ", ".join(f"{c}=%s" for c in vals)
                conn.execute(f"UPDATE workshops SET {sets}{', ' if sets else ''}sequence=sequence+1, updated_at=now() "
                             "WHERE id=%s", [*vals.values(), wid])
            conn.commit()
        return int(wid)

    def add_usage(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO usage_events (sid, signed_in, kind, target, detail, n) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(r.get("sid", ""), bool(r.get("signed_in")), r["kind"], r.get("target", ""), r.get("detail", ""),
                      r.get("n")) for r in rows])
            conn.commit()

    def usage_since(self, since: datetime) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return conn.execute("SELECT at, sid, signed_in, kind, target, detail, n FROM usage_events WHERE at >= %s",
                                (since,)).fetchall()

    def purge_usage(self, before: datetime) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM usage_events WHERE at < %s", (before,))
            conn.commit()
            return cur.rowcount

    def set_course_stats(self, counts: dict[int, int]) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO course_stats (course_id, enrolled, checked_at) VALUES (%s, %s, now()) "
                    "ON CONFLICT (course_id) DO UPDATE SET enrolled=EXCLUDED.enrolled, checked_at=now()",
                    list(counts.items()))
            conn.commit()

    def course_stats(self) -> dict[int, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT course_id, enrolled, checked_at FROM course_stats").fetchall()
        return {r["course_id"]: {"enrolled": r["enrolled"], "checkedAt": r["checked_at"].isoformat()} for r in rows}


def import_v1_events(rows: list[dict[str, Any]], offerings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """v1 coordination-tool events -> workshop docs (public fields only; never Zoom links or registrants)."""
    out = []
    for e in rows:
        days = e.get("days") or [{"date": e["start_date"]}]
        sessions = [{"start": f"{d['date']}T{d.get('start_time') or e.get('start_time') or '10:00'}",
                     "end": f"{d['date']}T{d.get('end_time') or e.get('end_time') or '11:30'}"} for d in days]
        off = offerings.get(e.get("slug") or "", {})
        status = {"confirmed": "scheduled", "delivered": "scheduled", "closed": "scheduled",
                  "cancelled": "cancelled"}.get(e.get("status") or "", "draft")
        out.append({
            "source_key": f"v1:{e['id']}", "offering": e.get("slug") or "", "title": e["title"],
            "summary": (e.get("notes_public") or off.get("description") or "").strip(),
            "program_tags": json.loads(e.get("program_tags") or "[]"), "speakers": e.get("speakers") or "",
            "format": e.get("format") or "webinar", "timezone": e.get("timezone") or "America/Chicago",
            "sessions": sessions, "status": status, "register_url": e.get("reg_form_url") or "",
            "calendar_url": e.get("calendar_url") or "",
            "moodle_url": (e.get("moodle_url") or "") if "/course/view.php" in (e.get("moodle_url") or "") else "",
            "recording_url": "",
        })
    return out


def retention_cutoff(days: int = 395) -> datetime:
    return _now() - timedelta(days=days)
