"""Harness API. Serves the same documents as the static snapshot, at the same paths minus `.json`.

Run:  STORE=files STORE_PATH=dist/api/v1 uvicorn harness.api:app
      STORE=postgres DATABASE_URL=postgresql://... uvicorn harness.api:app
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AuthConfig, MemoryAuthStore, PgAuthStore, Profile, http_token_exchange, make_router
from .admin import admin_usernames_from_env, make_admin_router, make_is_admin
from .crosswalk import Result, moodle_call_from_env, moodle_lookup_from_env, resolve
from .learner import MemoryEnrolLog, PgEnrolLog, catalog_courses, make_learner_router
from .live_store import MemoryLiveStore, PgLiveStore
from .workshops import make_workshops_router
from .analytics import make_analytics_router
from .coord import CoordStore
from .coord_web import coordinator_usernames_from_env, make_coord_router, make_is_coordinator
from .contract import spec
from .paths import MemoryPathStore, PgPathStore, answer_counts, load_config as load_paths, make_paths_router
from .store import FileStore, PgStore, Store

IMPLEMENTED_LATER_PHASE = {"getMe", "listCrosswalkReview", "decideCrosswalkReview", "getMyProgress", "createEnrollment",
                           "listEvents", "getPath", "getMyPath", "saveMyPath", "deleteMyPath"}


def make_store() -> Store:
    kind = os.environ.get("STORE", "files")
    if kind == "postgres":
        return PgStore(os.environ["DATABASE_URL"])
    return FileStore(os.environ.get("STORE_PATH", "dist/api/v1"))


WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(store: Store | None = None, auth_store=None, auth_config=None, exchange=None,
               web_dir: Path | None = None, lookup=None,
               admin_usernames: set[str] | None = None, moodle_call=None, enrol_log=None,
               live_store=None, coord_store=None, coordinator_usernames: set[str] | None = None,
               path_store=None, paths_config=None) -> FastAPI:
    app = FastAPI(title="NCSA Training Platform API", version="0.1.0", docs_url="/api/docs", openapi_url=None)
    app.state.store = store or make_store()

    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"])

    def st() -> Store:
        return app.state.store

    if auth_store is None:
        auth_store = PgAuthStore(app.state.store.dsn) if isinstance(app.state.store, PgStore) else MemoryAuthStore()
    app.state.auth_store = auth_store
    get_cfg = (lambda: auth_config) if auth_config is not None else AuthConfig.from_env
    def linker(p: Profile) -> Result:
        lk = lookup if lookup is not None else moodle_lookup_from_env()
        if lk is None:
            return Result("unavailable", reason="not_configured")
        return resolve(lk, p.identities, p.preferred_username, p.email)

    is_admin = make_is_admin(lambda: {a.lower() for a in admin_usernames} if admin_usernames is not None
                             else admin_usernames_from_env())
    if live_store is None:
        live_store = PgLiveStore(app.state.store.dsn) if isinstance(app.state.store, PgStore) else MemoryLiveStore()
    app.state.live_store = live_store
    auth_router = make_router(get_cfg, auth_store, exchange or http_token_exchange, linker, is_admin,
                              on_signin=lambda: live_store.add_usage([{"sid": "", "signed_in": True, "kind": "signin"}]))
    app.include_router(auth_router)
    app.include_router(make_admin_router(
        auth_store, auth_router.current_user, is_admin,
        get_base_url=lambda: (get_cfg().base_url if get_cfg() else None),
        get_moodle_url=lambda: os.environ.get("MOODLE_URL") or ((st().meta() or {}).get("source") or {}).get("siteUrl")))
    if enrol_log is None:
        enrol_log = PgEnrolLog(app.state.store.dsn) if isinstance(app.state.store, PgStore) else MemoryEnrolLog()
    app.state.enrol_log = enrol_log
    app.include_router(make_learner_router(
        get_call=(lambda: moodle_call) if moodle_call is not None else moodle_call_from_env,
        catalog_store=st, current_user=auth_router.current_user, log=enrol_log,
        get_base_url=lambda: (get_cfg().base_url if get_cfg() else None),
        get_moodle_url=lambda: os.environ.get("MOODLE_URL") or ((st().meta() or {}).get("source") or {}).get("siteUrl")))
    base_url = lambda: (get_cfg().base_url if get_cfg() else None)  # noqa: E731
    app.include_router(make_workshops_router(live_store, auth_router.current_user, is_admin, base_url))

    def catalog_titles() -> dict[str, Any]:
        tiles = st().tiles().get("items", [])
        return {"courses": {cid: v["title"] for cid, v in catalog_courses(st()).items()},
                "tiles": {t["id"]: t["title"] for t in tiles}}

    paths_cache: dict[str, Any] = {}

    def paths_cfg() -> dict[str, Any]:
        if paths_config is not None:
            return load_paths(paths_config)
        if "cfg" not in paths_cache:  # catalog/paths.yaml is read once per process (a deploy restarts)
            paths_cache["cfg"] = load_paths()
        return paths_cache["cfg"]

    if path_store is None:
        path_store = PgPathStore(app.state.store.dsn) if isinstance(app.state.store, PgStore) else MemoryPathStore()
    app.state.path_store = path_store
    app.include_router(make_paths_router(paths_cfg, path_store, live_store, lambda: catalog_titles()["tiles"],
                                         auth_router.current_user, base_url))
    app.include_router(make_analytics_router(live_store, auth_store, enrol_log.rows_since, catalog_titles,
                                             auth_router.current_user, is_admin, base_url,
                                             paths_report=lambda: answer_counts(path_store.all_paths(), paths_cfg())))
    if coord_store is None:
        coord_store = CoordStore(app.state.store.dsn) if isinstance(app.state.store, PgStore) else CoordStore()
    app.state.coord_store = coord_store
    is_coordinator = make_is_coordinator(is_admin, (lambda: {c.lower() for c in coordinator_usernames})
                                         if coordinator_usernames is not None else coordinator_usernames_from_env)
    app.include_router(make_coord_router(coord_store, live_store, auth_router.current_user, is_coordinator, is_admin,
                                         base_url))

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        meta = st().meta()
        return {"ok": meta is not None, "generatedAt": meta["generatedAt"] if meta else None}

    @app.get("/api/v1/openapi.yaml", include_in_schema=False)
    def contract() -> JSONResponse:
        return JSONResponse(spec())

    @app.get("/api/v1/catalog/meta")
    def catalog_meta() -> dict[str, Any]:
        meta = st().meta()
        if meta is None:
            raise HTTPException(503, detail="catalog not synced yet")
        return meta

    @app.get("/api/v1/categories")
    def categories() -> dict[str, Any]:
        return st().categories()

    @app.get("/api/v1/shelves")
    def shelves() -> dict[str, Any]:
        return st().shelves()

    @app.get("/api/v1/tiles")
    def tiles() -> dict[str, Any]:
        return st().tiles()

    @app.get("/api/v1/tiles/{tile_id}")
    def tile(tile_id: str) -> Any:
        t = st().tile(tile_id)
        if t is None:
            return JSONResponse({"error": "not_found", "message": f"no tile {tile_id}"}, status_code=404)
        return t

    # Later-phase stubs, straight from the contract's x-phase markers.
    for path, ops in spec()["paths"].items():
        for method, op in ops.items():
            phase = op.get("x-phase", 1)
            if phase <= 1 or op["operationId"] in IMPLEMENTED_LATER_PHASE or op.get("x-implemented"):
                continue

            def stub(_phase: int = phase, _op: str = op["operationId"]) -> JSONResponse:
                return JSONResponse(
                    {"error": "not_implemented", "message": f"{_op} is planned for phase {_phase}", "phase": _phase},
                    status_code=501,
                )

            app.add_api_route(f"/api/v1{path}", stub, methods=[method.upper()], name=op["operationId"])

    # The front door, served by the harness itself (same files as GitHub Pages; API is live, not .json).
    # Mounted last so every API and auth route above takes precedence. SERVE_WEB=0 turns it off.
    web = web_dir if web_dir is not None else Path(os.environ.get("WEB_DIR", WEB_DIR))
    if os.environ.get("SERVE_WEB", "1") != "0" and (web / "index.html").exists():
        base_cfg = json.loads((web / "config.json").read_text()) if (web / "config.json").exists() else {}

        @app.get("/config.json", include_in_schema=False)
        def web_config() -> JSONResponse:
            return JSONResponse({**base_cfg, "apiBase": "api/v1", "suffix": "", "signIn": True},
                                headers={"Cache-Control": "no-cache"})

        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")

        # Browsers otherwise reuse old app.js/styles.css after a deploy; no-cache means "revalidate"
        # (a cheap 304 via ETag), not "don't store".
        @app.middleware("http")
        async def revalidate_front_door(request, call_next):
            response = await call_next(request)
            path = request.url.path
            if not path.startswith(("/api/", "/auth", "/admin")) and "cache-control" not in response.headers:
                response.headers["Cache-Control"] = "no-cache"
            return response

    return app


app = create_app() if os.environ.get("HARNESS_NO_AUTOAPP") != "1" else None
