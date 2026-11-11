"""Learning paths from "From Laptop to Cluster" (option 2: links plus a saved path).

Laptop to Cluster (keeley4/intro_hpc, a static page) ends by asking what kind of work someone does and how
much of it, and names a best-fit resource. Its "See your learning path" button opens the front door at
  #/path?work=<work>&scale=<S|M|L>&fit=<resource>&ex=<research example>&par=<yes|notsure|no>&from=l2c
and the front door asks this API for the path. Signed-in learners can save the answers so the path stays
on My Desktop. What is stored: those five answers and two timestamps, keyed by the front-door account id.
No names, no emails, no free text. Analytics reports only counts.

  GET    /api/v1/paths?work=&scale=&fit=&ex=&par=   the path for these answers (public; no answers = the
                                                    general journey, every stage, for browsing)
  GET    /api/v1/me/path                            {saved: false} or {saved: true, answers, path}
  PUT    /api/v1/me/path   {work, scale, fit, ex, par}  save (same-site, signed in)
  DELETE /api/v1/me/path                            forget the saved answers

The mapping lives in catalog/paths.yaml (hand-edited, like shelves.yaml).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .admin import _same_origin
from .workshops import public_doc

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "catalog" / "paths.yaml"
ANSWER_KEYS = ("work", "scale", "fit", "ex", "par")


def _ref(ref: str) -> str:
    kind, _, key = str(ref).partition(":")
    return f"course-{int(key)}" if kind == "course" else f"{kind}-{key}"


def load_config(source: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(source, dict):
        raw = source
    else:
        p = Path(source or os.environ.get("PATHS_FILE") or DEFAULT_FILE)
        raw = yaml.safe_load(p.read_text()) if p.exists() else {}
    raw = raw or {}
    stages = []
    for i, s in enumerate(raw.get("stages") or []):
        # steps = the ordered main line (first = "start with this"); also = off the main line.
        # Older files list only `tiles`: those become the steps.
        # A step can be a list of alternatives: the first one on the front door is the step, the rest
        # become "also". Used to stage a replacement course (hidden in Learn@NCSA until reviewed) ahead
        # of the course it replaces, with no second edit when it is published.
        step_refs = s.get("steps") if s.get("steps") is not None else s.get("tiles")
        groups = [[_ref(x) for x in (t if isinstance(t, list) else [t])] for t in step_refs or []]
        steps = [g[0] for g in groups]
        alts = [x for g in groups for x in g[1:]]
        also = [_ref(t) for t in s.get("also") or [] if _ref(t) not in steps and _ref(t) not in alts]
        stages.append({"id": s["id"], "label": str(s.get("label") or i + 1), "title": s["title"],
                       "summary": s.get("summary", ""), "ready": s.get("ready") or "",
                       "l2cSteps": list(s.get("l2c_steps") or []),
                       "steps": steps, "also": also, "tiles": steps + alts + also, "groups": groups,
                       "offerings": list(s.get("offerings") or [])})
    ids = {s["id"] for s in stages}
    a = raw.get("answers") or {}
    fit_stage = {k: v for k, v in (raw.get("fit_stage") or {}).items()}
    bad = [v for v in fit_stage.values() if v is not None and v not in ids]
    if bad:
        raise ValueError(f"paths.yaml: fit_stage points at unknown stages {bad}")
    return {
        "stages": stages,
        "answers": {k: {str(x["id"]): x.get("name", x["id"]) for x in a.get(k) or []} for k in ANSWER_KEYS},
        "fit_stage": fit_stage,
        "scale_up": {k: v for k, v in (raw.get("scale_up") or {}).items()},
        "fit_tiles": {k: [_ref(t) for t in v or []] for k, v in (raw.get("fit_tiles") or {}).items()},
        "work_tiles": {k: [_ref(t) for t in v or []] for k, v in (raw.get("work_tiles") or {}).items()},
        "par_tiles": {k: [_ref(t) for t in v or []] for k, v in (raw.get("par_tiles") or {}).items()},
        "fit_offerings": {k: list(v or []) for k, v in (raw.get("fit_offerings") or {}).items()},
        "work_offerings": {k: list(v or []) for k, v in (raw.get("work_offerings") or {}).items()},
        "no_path_note": raw.get("no_path_note", ""),
        "l2c_url": raw.get("l2c_url", ""),
        "focus_max": int(raw.get("focus_max", 4)),
    }


def clean_answers(cfg: dict[str, Any], q: dict[str, Any]) -> dict[str, str]:
    """Only known values are accepted. work, scale and fit are required; ex and par are optional."""
    out: dict[str, str] = {}
    for k in ANSWER_KEYS:
        v = str(q.get(k) or "").strip()
        if not v:
            if k in ("work", "scale", "fit"):
                raise ValueError(f"{k} is required")
            continue
        if v not in cfg["answers"][k]:
            raise ValueError(f"unknown {k}")
        out[k] = v
    return out


def _dedupe(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in xs if not (x in seen or seen.add(x))]


def _resolve_alternatives(cfg: dict[str, Any], tile_titles: dict[str, str]) -> dict[str, Any]:
    """Pick each step group's first course that is on the front door; the others move to the top of "also"."""
    stages = []
    swap: dict[str, str] = {}
    for s in cfg["stages"]:
        groups = s.get("groups") or [[t] for t in s["steps"]]
        steps, moved = [], []
        for g in groups:
            live = [t for t in g if t in tile_titles]
            pick = live[0] if live else g[0]
            steps.append(pick)
            moved += [t for t in g if t != pick]
        also = moved + [t for t in s["also"] if t not in moved]
        stages.append({**s, "steps": steps, "also": also, "tiles": steps + also})
        for g in groups:  # a pick that names a replaced course means its replacement
            pick = next((t for t in g if t in tile_titles), None)
            for t in g:
                if pick and t != pick:
                    swap[t] = pick
    return {**cfg, "stages": stages, "swap": swap}


def build_path(cfg: dict[str, Any], ans: dict[str, str] | None, tile_titles: dict[str, str],
               workshops: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Stages in Laptop to Cluster order, the stage the learner is at, and up to focus_max "do these next" tiles.
    Tiles not on the published front door are dropped, so a course that leaves the catalog drops out.
    With no answers (ans None or {}) this is the general journey: every stage, no "here", no focus."""
    now = now or datetime.now(timezone.utc)
    cfg = _resolve_alternatives(cfg, tile_titles)
    ans = ans or {}
    general = not ans
    here = None if general else cfg["fit_stage"].get(ans["fit"])
    up = {} if general else (cfg["scale_up"].get(ans["fit"]) or {})
    if here and ans["scale"] in (up.get("scales") or []):
        here = up["stage"]
    upcoming = []
    for w in workshops:
        if w.get("status") != "scheduled" or not w.get("sessions"):
            continue
        d = public_doc(w)
        if datetime.fromisoformat(d["sessions"][-1]["end"]) >= now:
            upcoming.append(d)
    upcoming.sort(key=lambda d: d["sessions"][0]["start"])

    def events_for(slugs: list[str]) -> list[dict[str, Any]]:
        return [d for d in upcoming if d["offering"] and d["offering"] in slugs]

    ids = [s["id"] for s in cfg["stages"]]
    pos = ids.index(here) if here in ids else -1
    stages = []
    for i, s in enumerate(cfg["stages"]):
        stages.append({"id": s["id"], "label": s["label"], "title": s["title"], "summary": s["summary"],
                       "ready": s["ready"], "l2cSteps": s["l2cSteps"],
                       "position": "none" if pos < 0 else ("here" if i == pos else ("before" if i < pos else "after")),
                       "stepIds": [t for t in s["steps"] if t in tile_titles],
                       "alsoIds": [t for t in s["also"] if t in tile_titles],
                       "tileIds": [t for t in s["tiles"] if t in tile_titles],
                       "workshops": events_for(s["offerings"])})
    # Where each tile sits on the journey: (stage index, 0 for a step / 1 for "also", position).
    where: dict[str, tuple[int, int, int]] = {}
    for si, s in enumerate(cfg["stages"]):
        for kind, refs in ((0, s["steps"]), (1, s["also"])):
            for j, t in enumerate(refs):
                where.setdefault(t, (si, kind, j))
    focus = []
    if here:
        focus = cfg["fit_tiles"].get(ans["fit"], []) + cfg["work_tiles"].get(ans["work"], [])
        if ans.get("par"):
            focus += cfg["par_tiles"].get(ans["par"], [])
        if not focus:
            focus = next(s["steps"] for s in cfg["stages"] if s["id"] == here)
    focus = [t for t in _dedupe([cfg.get("swap", {}).get(t, t) for t in focus]) if t in tile_titles]
    # A pick partway along a stage brings in the steps before it, so the picks start where the stage starts
    # ("Start with this") and "take these in order" agrees with the full path.
    filled: list[str] = []
    for t in focus:
        w = where.get(t)
        if w and w[1] == 0:
            filled += [s for s in cfg["stages"][w[0]]["steps"][: w[2]] if s in tile_titles]
        filled.append(t)
    focus = _dedupe(filled)
    # Journey order, so "do these next" reads as a sequence and agrees with "you are here".
    # Tiles on no stage keep their candidate order, after the staged ones.
    order = {t: i for i, t in enumerate(focus)}
    focus.sort(key=lambda t: (where.get(t, (len(cfg["stages"]), 0, 0)), order[t]))
    focus = focus[: cfg["focus_max"]]
    focus_stage = {t: cfg["stages"][where[t][0]]["label"] for t in focus if t in where}
    slugs = [] if general else _dedupe(cfg["fit_offerings"].get(ans["fit"], []) + cfg["work_offerings"].get(ans["work"], []))
    return {
        "general": general,
        "answers": ans,
        "labels": {k: cfg["answers"][k][v] for k, v in ans.items()},
        "here": here,
        "note": "" if here or general else cfg["no_path_note"],
        "focus": focus,
        "focusStage": focus_stage,
        "focusWorkshops": events_for(slugs)[:3] if here else [],
        "stages": stages,
        "l2cUrl": cfg["l2c_url"],
    }


def answer_counts(rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """Aggregate counts of saved paths for the admin analytics page (no user ids)."""
    from collections import Counter
    def named(k: str) -> list[dict[str, Any]]:
        c = Counter(r.get(k) for r in rows if r.get(k))
        return [{"id": v, "name": cfg["answers"][k].get(v, v), "count": n} for v, n in c.most_common()]
    combos = Counter((r["work"], r["scale"]) for r in rows)
    return {"saved": len(rows), "work": named("work"), "scale": named("scale"), "fit": named("fit"),
            "example": named("ex"), "par": named("par"),
            "combos": [{"work": cfg["answers"]["work"].get(w, w), "scale": cfg["answers"]["scale"].get(s, s), "count": n}
                       for (w, s), n in combos.most_common(10)]}


class MemoryPathStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get_path(self, user_id: str) -> dict[str, Any] | None:
        r = self.rows.get(str(user_id))
        return dict(r) if r else None

    def save_path(self, user_id: str, ans: dict[str, str]) -> None:
        now = datetime.now(timezone.utc)
        old = self.rows.get(str(user_id))
        self.rows[str(user_id)] = {**{k: ans.get(k, "") for k in ANSWER_KEYS},
                                   "saved_at": old["saved_at"] if old else now, "updated_at": now}

    def delete_path(self, user_id: str) -> bool:
        return self.rows.pop(str(user_id), None) is not None

    def all_paths(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows.values()]


class PgPathStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _conn(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def get_path(self, user_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            return conn.execute("SELECT work, scale, fit, ex, par, saved_at, updated_at FROM learner_paths "
                                "WHERE user_id=%s", (str(user_id),)).fetchone()

    def save_path(self, user_id: str, ans: dict[str, str]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO learner_paths (user_id, work, scale, fit, ex, par) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET work=EXCLUDED.work, scale=EXCLUDED.scale, fit=EXCLUDED.fit, "
                "ex=EXCLUDED.ex, par=EXCLUDED.par, updated_at=now()",
                (str(user_id), *(ans.get(k, "") for k in ANSWER_KEYS)))
            conn.commit()

    def delete_path(self, user_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM learner_paths WHERE user_id=%s", (str(user_id),))
            conn.commit()
            return cur.rowcount == 1

    def all_paths(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return conn.execute("SELECT work, scale, fit, ex, par FROM learner_paths").fetchall()


def make_paths_router(get_cfg: Callable[[], dict[str, Any]], store: Any, live: Any,
                      tile_titles: Callable[[], dict[str, str]], current_user: Callable[[Request], Any],
                      get_base_url: Callable[[], str | None]) -> APIRouter:
    r = APIRouter()
    nocache = {"Cache-Control": "no-store"}

    def path_for(ans: dict[str, str]) -> dict[str, Any]:
        return build_path(get_cfg(), ans, tile_titles(), live.list_workshops())

    @r.get("/api/v1/paths")
    def public_path(request: Request) -> Response:
        q = dict(request.query_params)
        try:
            ans = clean_answers(get_cfg(), q) if any(q.get(k) for k in ANSWER_KEYS) else {}
        except ValueError as exc:
            return JSONResponse({"error": "bad_request", "message": str(exc)}, 400)
        return JSONResponse(path_for(ans), headers={"Cache-Control": "public, max-age=300"})

    @r.get("/api/v1/me/path")
    def my_path(request: Request) -> Response:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        row = store.get_path(user["id"])
        if not row:
            return JSONResponse({"saved": False}, headers=nocache)
        try:
            ans = clean_answers(get_cfg(), row)
        except ValueError:  # the mapping changed under a saved path
            return JSONResponse({"saved": False, "stale": True}, headers=nocache)
        at = row.get("updated_at")
        return JSONResponse({"saved": True, "updatedAt": at.isoformat() if hasattr(at, "isoformat") else at,
                             "answers": ans, "path": path_for(ans)}, headers=nocache)

    @r.put("/api/v1/me/path")
    async def save(request: Request) -> Response:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        if not _same_origin(request, get_base_url()):
            return JSONResponse({"error": "forbidden", "message": "Cross-site request refused."}, 403)
        try:
            body = await request.json()
            ans = clean_answers(get_cfg(), body if isinstance(body, dict) else {})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": "bad_request", "message": str(exc)}, 400)
        store.save_path(user["id"], ans)
        live.add_usage([{"sid": "", "signed_in": True, "kind": "path_save", "target": ans["fit"],
                         "detail": f'{ans["work"]}:{ans["scale"]}'}])
        return JSONResponse({"saved": True, "answers": ans, "path": path_for(ans)}, headers=nocache)

    @r.delete("/api/v1/me/path")
    def forget(request: Request) -> Response:
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "signin_required", "message": "Please sign in."}, 401)
        if not _same_origin(request, get_base_url()):
            return JSONResponse({"error": "forbidden", "message": "Cross-site request refused."}, 403)
        store.delete_path(user["id"])
        return JSONResponse({"saved": False}, headers=nocache)

    return r
