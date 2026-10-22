"""FastAPI app for `pcui serve` (frontend/design.md §4): DTO-mapped query endpoints over the
worker pool, the session/artifact store with optimistic locking + SSE, and the bootstrap
security gate. Every skill call is a whitelisted command with controlled argument
construction — no argv/path passthrough from the client, ever."""
from __future__ import annotations

import asyncio
import json
import re
import time
import hashlib
import math
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

from . import mappers
from .capabilities import assemble
from .openapi import install_openapi
from .paths import META_DATA
from .security import COOKIE_NAME, Security
from .session_gates import gate_artifacts
from .store import Conflict, NotFound, Store
from .team_diagnose import validate_team
from .team_matchup import MatchupInputError, run_actual_matchup
from .team_tune import TuneInputError, run_team_tune
from .worker import WorkerError, WorkerPool

MAX_BODY = 2 * 1024 * 1024
QUERY_TIMEOUT = 60.0
TEAM_TIMEOUT = 600.0
SSE_PING_SECONDS = 15.0
SSE_RETRY_MS = 3000
# One wait poll stays well under the CDN-free loopback's limits and the CLI's own read timeout; the
# CLI polls again at once, so a waiter counts as present for a poll plus this grace.
WAIT_POLL_SECONDS = 25.0
WAIT_GRACE_SECONDS = 15.0
# A failed operator run stays on the session for this long, so the reader sees what went wrong.
FAILED_RUN_SECONDS = 600.0
PANEL_KINDS = ("decision", "intake-answer")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")
_DEPLOYMENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,80}\Z")

DEX_KINDS = ("pokemon", "move", "item", "ability", "nature")
DEX_MAPPERS = {"pokemon": mappers.map_pokemon, "move": mappers.map_move,
               "item": mappers.map_item, "ability": mappers.map_ability,
               "nature": mappers.map_nature}
# team ops the bridge accepts inside a session spec (deterministic operators only, P0).
TEAM_OPS = {"parse", "validate", "diagnose", "matchup", "select", "repset", "observed",
            "landscape", "search", "show", "oppmatrix", "intake", "context-audit", "frame",
            "slate-evaluate", "checkpoint", "draft-init", "fill", "answer-audit", "tune",
            "replace", "vocab"}
# Session-op fields team.py treats as LOCAL FILE PATHS. The bridge never forwards client
# strings here (arbitrary-file read, §4.3); clients send inline JSON and the bridge
# materializes it into controlled temp files.
TEAM_FILE_FIELDS = ("file", "context", "slate", "frame_output", "slate_output",
                    "audit_receipt")


def _raw_or_error(doc: Any) -> Any:
    """Pass a CLI error shape through as a 404/400 instead of mapping it."""
    if isinstance(doc, dict) and doc.get("error"):
        code = doc["error"].get("code", "bad_input")
        raise HTTPException(404 if code == "not_found" else 400, detail=doc)
    return doc


# The per-slot placeholder a batch endpoint puts where a non-object input was, so the input→result
# position contract holds. Matches the uniform ErrorShape (ok:false + error) the batch DTOs validate
# against (read-only; safe to share one instance across slots).
_BAD_INPUT_ITEM = {"ok": False, "error": {"code": "bad_input", "message": "item must be an object"}}


_RELATIVE_REF_RE = re.compile(r"""((?:src|href)=["'])\./""")


class _SpaStaticFiles(StaticFiles):
    """Static SPA hosting: unknown paths fall back to index.html so client-side routes
    survive a refresh (the /api and /projection mounts resolve first). Starlette signals
    a missing file either as a 404 response or an HTTPException depending on version —
    handle both."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        from starlette.exceptions import HTTPException as StarletteHTTPException
        # A stale tab may request a chunk from the previous Vite build. Returning index.html
        # with 200 for /assets/*.js turns that into a misleading module/MIME error; keep the
        # real 404 so the client can offer a reload while SPA routes still fall back below.
        asset_path = path.lstrip("./\\")
        if asset_path == "assets" or asset_path.startswith(("assets/", "assets\\")):
            return await super().get_response(path, scope)
        if asset_path == "favicon.ico":
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-store"
            return response
        served_index = False
        try:
            response = await super().get_response(path, scope)
            if response.status_code == 404:
                response = await super().get_response("index.html", scope)
                served_index = True
        except StarletteHTTPException as e:
            if e.status_code != 404:
                raise
            response = await super().get_response("index.html", scope)
            served_index = True
        # The SPA entry must revalidate on every load: it names the current hashed JS/CSS bundles, so
        # a heuristically cached index.html leaves a new deploy invisible until a hard refresh (same
        # class of bug as the client-side manifest no-store fix). The bundles themselves are content-
        # hashed and stay immutable-cacheable.
        # "." is what Starlette's normpath yields for a bare "/" request; "" / "index.html" cover the
        # other direct hits, and served_index covers client-route fallbacks.
        if served_index and response.status_code == 200:
            # A deploy-neutral build names its bundles `./assets/...`. Served as the fallback for a
            # nested client route (`/meta/<slug>`), that resolves under the route (`/meta/assets/`)
            # and every bundle comes back as this same HTML. The SPA is always mounted at the root,
            # so root the relative references; a release-bound index uses absolute scoped URLs and
            # passes through unchanged.
            index = Path(self.directory or ".") / "index.html"
            html = _RELATIVE_REF_RE.sub(r"\1/", index.read_text(encoding="utf-8"))
            response = Response(html, media_type="text/html")
        if served_index or path in ("", ".", "index.html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(store: Store, pool: WorkerPool, security: Security,
               deployment_id: str | None = None,
               dist_dir: Path | None = None,
               projection_dir: Path | None = None,
               bind_host: str = "127.0.0.1", workspace=None) -> FastAPI:
    if deployment_id is not None and not _DEPLOYMENT_ID_RE.fullmatch(deployment_id):
        raise ValueError(f"invalid deployment id: {deployment_id!r}")
    app = FastAPI(title="pcui bridge", docs_url=None, redoc_url=None, openapi_url=None)
    # The projection's matchup grids are large, highly repetitive JSON (the variant-expanded doubles
    # KO grid is ~13 MB raw) and compress to 6-8% of that. Serving them uncompressed would send
    # ~13 MB where ~0.8 MB suffices, so compression is not an optimisation here — it is what makes
    # shipping the full grid viable at all.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    state: dict[str, Any] = {"capabilities": None}
    caps_lock = asyncio.Lock()               # assemble the ~40MB digest at most once, even
    #                                          under concurrent first requests
    trend_cache: dict[str, tuple[float, Any]] = {}   # format -> (mtime, mapped TrendDto)
    # format -> (mtime, compact raw store, mapped per-Pokemon DTOs)
    usage_trend_cache: dict[str, tuple[float, dict, dict[str, Any]]] = {}
    ko_trend_cache: dict[str, tuple[float, dict, dict[str, Any]]] = {}
    oppcache_cache: dict[str, tuple[float, Any]] = {}   # "format:view" -> (mtime, mapped DTO)
    learners_cache: dict[str, Any] = {"doc": None}      # read-only dex data: build once per process
    # format -> (mtime, raw KO doc, row-by-slug, mapped per-Pokemon DTOs)
    ko_cache: dict[str, tuple[float, dict, dict, dict[str, Any]]] = {}
    # format -> (input mtimes, {docs, mapped rankings by scope, mapped details by slug})
    top_meta_cache: dict[str, tuple[tuple, dict[str, Any]]] = {}
    # format -> ((details mtime, ko mtime), facets DTO)
    facets_cache: dict[str, tuple[tuple[float, float], Any]] = {}
    # (season, format) -> {English canonical: usage rank}; the KO panels pair each opponent with
    # how often it is actually played, which is what lets a reader discount pure exposure.
    usage_rank_cache: dict[tuple[str, str], tuple[float, dict[str, int]]] = {}
    subscribers: set[asyncio.Queue] = set()
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    # When the operator deliberately binds beyond localhost (cli.py prints a warning and
    # requires typed consent), the localhost host/origin allowlist would otherwise reject
    # every real request — so relax it for the consented bind and rely on the CLI-secret /
    # cookie auth. A localhost bind keeps the strict anti-DNS-rebinding gate.
    allow_any_host = not security.host_allowed(bind_host)

    async def query(cli_id: str, argv: list[str], stdin: str | None = None,
                    timeout: float = QUERY_TIMEOUT) -> Any:
        """Run a worker query and parse it, mapping failures to structured error responses
        instead of a bare 500: a worker timeout -> 504, unparseable CLI output -> 502."""
        try:
            return await asyncio.to_thread(pool.request_json, cli_id, argv, stdin, timeout)
        except WorkerError as e:
            raise HTTPException(504, {"error": {"code": "bad_input", "message": str(e)}})
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(502, {"error": {"code": "bad_input",
                                     "message": f"{cli_id} produced no parseable output"}})

    def publish(name: str | None, payload: dict) -> None:
        """Queue one SSE message for every open stream; unnamed messages are artifact appends."""
        loop = loop_ref.get("loop")
        if loop is None:
            return
        for q in list(subscribers):
            loop.call_soon_threadsafe(q.put_nowait, (name, payload))

    # What the daemon itself sees happening in a session, never stored: an operator it is running
    # for the assistant (`pcui run --session`), and an assistant blocked in `pcui session wait`.
    # Both are live observations, so they may say "running" or "waiting" where the ledger cannot.
    activity: dict[str, dict] = {}
    waiters: dict[str, dict] = {}
    picked: dict[str, int] = {}         # session -> newest panel answer sequence a wait handed over
    wakers: dict[str, set[asyncio.Event]] = {}
    active_runs: set[object] = set()

    def on_event(event: dict) -> None:
        publish(None, event)
        loop = loop_ref.get("loop")
        for waker in list(wakers.get(event["sessionId"], ())):
            if loop is not None:
                loop.call_soon_threadsafe(waker.set)

    store.subscribe(on_event)

    def live_view(view: dict) -> dict:
        now = time.time()
        run = activity.get(view["id"])
        if run and run["state"] == "failed" and now - run["ended"] > FAILED_RUN_SECONDS:
            activity.pop(view["id"], None)
            run = None
        if run:
            view["activity"] = {key: value for key, value in run.items() if key not in ("started", "ended")}
        wait = waiters.get(view["id"])
        if wait and wait["until"] > now:
            view["waiting"] = {"kinds": wait["kinds"], "until": _iso(wait["until"])}
        if view["id"] in picked:
            view["pickedUp"] = picked[view["id"]]
        return view

    def close_workspace():
        if workspace is not None:
            store.close()
            workspace.close()
    app.state.close_workspace = close_workspace

    def workspace_call(action):
        from .workspace import WorkspaceError
        if workspace is None:
            raise HTTPException(503, "workspace not configured")
        try:
            return action()
        except WorkspaceError as exc:
            raise HTTPException(exc.status, {"error": {"code": exc.code, "message": exc.code}}) from exc
        except OSError as exc:
            raise HTTPException(422, {"error": {"code": "workspace_io_error", "message": "Workspace I/O failed"}}) from exc

    @app.get("/api/workspace")
    async def workspace_info():
        return workspace_call(lambda: workspace.info())

    @app.post("/api/workspace/pick-folder")
    async def pick_workspace_folder(body: dict):
        from .folder_picker import choose_folder
        from .workspace import WorkspaceError
        def validate():
            workspace.check(body.get("workspaceId"))
            if not isinstance(body.get("lang"), str) or body["lang"] not in {"zh", "en", "ja"}:
                raise WorkspaceError("bad_input", 400)
            return workspace.project
        initial = workspace_call(validate)
        # The helper owns its GUI thread; it never receives or touches a Store connection.
        try:
            selected = await asyncio.to_thread(choose_folder, initial, body["lang"])
        except WorkspaceError as exc:
            raise HTTPException(exc.status, {"error": {"code": exc.code, "message": exc.code}}) from exc
        workspace_call(lambda: workspace.check(body.get("workspaceId")))
        return {"path": selected}

    @app.post("/api/workspace/switch")
    async def switch_workspace(body: dict):
        nonlocal workspace, store
        def switch():
            workspace.check(body.get("workspaceId"))
            if active_runs:
                from .workspace import WorkspaceError
                raise WorkspaceError("workspace_busy")
            if not isinstance(body.get("path"), str) or not isinstance(body.get("migrate"), bool):
                from .workspace import WorkspaceError
                raise WorkspaceError("bad_input", 400)
            return workspace.switch(body["path"], body["migrate"], store)
        next_workspace, next_store = workspace_call(switch)
        if next_workspace is not workspace:
            old_workspace, old_store = workspace, store
            workspace, store = next_workspace, next_store
            store.subscribe(on_event)
            old_store.close(); old_workspace.close()
            activity.clear(); waiters.clear(); picked.clear(); diagnosis_reports.clear()
            for events in wakers.values():
                for event in events:
                    event.set()
        return workspace.info()

    @app.get("/api/workspace/library")
    async def workspace_library():
        return workspace_call(lambda: workspace.library())

    @app.post("/api/workspace/import-browser")
    async def import_browser_library(body: dict):
        workspace_call(lambda: workspace.import_browser(body))
        return {"ok": True}

    @app.post("/api/workspace/library")
    async def save_workspace_library(body: dict):
        return workspace_call(lambda: workspace.put_library(body))

    @app.get("/api/workspace/records")
    async def workspace_records():
        return workspace_call(lambda: {"workspaceId": workspace.identity, "records": workspace.records()})

    @app.post("/api/workspace/records")
    async def save_workspace_record(body: dict):
        if not isinstance(body.get("record"), dict):
            raise HTTPException(400, "record required")
        workspace_call(lambda: workspace.edit_records(body.get("workspaceId"), record=body["record"]))
        return {"ok": True}

    @app.post("/api/workspace/records/delete")
    async def delete_workspace_records(body: dict):
        if not isinstance(body.get("ids"), list) or any(not isinstance(value, str) for value in body["ids"]):
            raise HTTPException(400, "ids required")
        workspace_call(lambda: workspace.edit_records(body.get("workspaceId"), ids=body["ids"]))
        return {"ok": True}

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if not allow_any_host:
            if not security.host_allowed(request.headers.get("host")):
                return Response(status_code=403, content="forbidden host")
            if not security.origin_allowed(request.headers.get("origin")):
                return Response(status_code=403, content="forbidden origin")
        # Body-size gate. Parse Content-Length defensively (a bogus value must be a 400, not a
        # 500), and require it on body-bearing methods: a chunked request carries no
        # Content-Length and would otherwise bypass MAX_BODY entirely with unbounded buffering.
        cl = request.headers.get("content-length")
        try:
            length = int(cl) if cl is not None else None
        except ValueError:
            return Response(status_code=400, content="invalid content-length")
        large_workspace_body = request.url.path.startswith("/api/workspace/records") or request.url.path == "/api/workspace/import-browser"
        body_limit = 32 * 1024 * 1024 if large_workspace_body else MAX_BODY
        if length is not None and length > body_limit:
            # Drain a BOUNDED oversize before answering so the client's send() completes and can READ
            # this 413, instead of the Windows connection RST it sees when the socket closes with the
            # request body still unread. The pcui client also pre-checks size (cli.MAX_REQUEST_BODY);
            # this backstops other callers. Absurd bodies (> 4x the cap) are left to reset (rare).
            if length <= 4 * body_limit:
                await request.body()
            return Response(status_code=413, media_type="application/json",
                            content=json.dumps({"error": {"code": "body_too_large", "limit": body_limit}}),
                            headers={"Connection": "close"})
        if length is None and request.method in ("POST", "PUT", "PATCH"):
            return Response(status_code=411, content="length required")
        # Authentication guards the API only; static assets (dist, projection, runtime
        # config) are public data the SPA needs BEFORE it can bootstrap a cookie.
        if request.url.path.startswith("/api/") and request.url.path != "/api/bootstrap":
            if not security.is_authenticated(request.cookies.get(COOKIE_NAME),
                                             request.headers.get("x-pcui-secret")):
                return Response(status_code=401, content="unauthenticated")
            expected_workspace = request.headers.get("x-pcui-workspace")
            if workspace is not None and expected_workspace and expected_workspace != workspace.identity:
                return JSONResponse(status_code=409, content={"detail": {"error": {"code": "workspace_changed", "message": "Workspace changed"}}})
        return await call_next(request)

    @app.get("/runtime-config.json")
    async def runtime_config():
        return {"runtime": "local", "apiBase": "/api", "projectionBase": "/projection"}

    @app.get("/api/health")
    async def health():
        # O(1) liveness probe for the CLI's daemon check — avoids pulling the whole session
        # list (an N+1 that grew with history) just to confirm the daemon answers.
        return {"ok": True}

    # -- bootstrap / capabilities -----------------------------------------------------

    @app.post("/api/bootstrap")
    async def bootstrap(body: dict, response: Response):
        cookie = security.consume_bootstrap(str(body.get("token", "")))
        if cookie is None:
            raise HTTPException(401, "invalid or already-used bootstrap token")
        response.set_cookie(COOKIE_NAME, cookie, httponly=True, samesite="strict")
        return {"ok": True}

    @app.post("/api/bootstrap/mint")
    async def mint_bootstrap(request: Request):
        # Same-machine file trust only: a browser cookie cannot mint sign-in links for others.
        if not security.is_cli(request.headers.get("x-pcui-secret")):
            raise HTTPException(403, "the CLI secret is required")
        return {"token": security.mint_bootstrap()}

    async def assembled_capabilities() -> dict:
        async with caps_lock:                  # assembled once; the lock stops two concurrent
            if state["capabilities"] is None:  # first requests from each running the full pass
                report: dict[str, Any] = {}
                state["capabilities"] = await asyncio.to_thread(
                    assemble, pool, deployment_id, None, report)
                state["projection_stale"] = bool(report.get("projectionStale"))
        return state["capabilities"]

    @app.get("/api/capabilities")
    async def capabilities():
        return await assembled_capabilities()

    @app.get("/api/console")
    async def console_status():
        """What the Console's setup card shows: whether an assistant can reach this bridge, and
        whether the static pages were built from the skill data being served."""
        from .agent_setup import companion_report, pcui_command
        await assembled_capabilities()
        return {"pcuiCommand": pcui_command(), "companion": companion_report(),
                "projectionStale": state.get("projection_stale", False)}

    # -- dex ---------------------------------------------------------------------------

    @app.post("/api/dex/{kind}")
    async def dex_query(kind: str, body: dict):
        if kind not in DEX_KINDS:
            raise HTTPException(404, "unknown dex kind")
        name = str(body.get("name", ""))[:200]
        if not name:
            raise HTTPException(400, "name required")
        if name.startswith("-"):
            # A leading '-' would be parsed as a CLI flag (argparse SystemExit, empty stdout)
            # rather than a query term; no dex name starts with one. Reject up front.
            raise HTTPException(400, "name must not start with '-'")
        doc = await query("dex", [kind, name, "--format", "json"])
        return DEX_MAPPERS[kind](_raw_or_error(doc))

    @app.get("/api/dex/learners")
    async def dex_learners():
        """Move -> learners index for the browse filter. Cached for the process: the dex database
        is read-only shipped data, so this cannot go stale under a running bridge."""
        def _build() -> Any:
            if learners_cache.get("doc") is None:
                learners_cache["doc"] = mappers.learners_index()
            return learners_cache["doc"]

        return await asyncio.to_thread(_build)

    @app.post("/api/dex/resolve/batch")
    async def dex_resolve(body: dict):
        names = [str(n)[:200] for n in (body.get("names") or [])][:100]
        if not names:
            raise HTTPException(400, "names required")
        if any(n.startswith("-") for n in names):
            raise HTTPException(400, "names must not start with '-'")
        argv = ["resolve", *names, "--format", "json"]
        kind = body.get("kind")
        if kind in DEX_KINDS:
            argv += ["--kind", kind]
        doc = await query("dex", argv)
        return [mappers.map_resolve_entry(e) for e in doc]

    # -- meta --------------------------------------------------------------------------

    def _usage_ranks(season: str, format: str) -> dict[str, int]:
        """English canonical -> this format's usage rank, cached on the ranking file's mtime."""
        path = META_DATA / f"ranking_{season}_{format}.json"
        if not path.exists():
            return {}
        mtime = path.stat().st_mtime
        cached = usage_rank_cache.get((season, format))
        if cached is None or cached[0] != mtime:
            rows = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
            cached = (mtime, {r["pokemon_en"]: r["rank"] for r in rows if r.get("pokemon_en")})
            usage_rank_cache[(season, format)] = cached
        return cached[1]

    @app.get("/api/meta/ranking")
    async def meta_ranking(format: str = "single", limit: int = 50):
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        limit = max(1, min(int(limit), 300))
        doc = await query("meta",
                          ["ranking", "--format", format, "--limit", str(limit),
                           "--output", "json"])
        return mappers.map_ranking(_raw_or_error(doc))

    @app.get("/api/meta/detail")
    async def meta_detail(format: str, pokemon: str):
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        doc = await query("meta",
                          ["detail", "--format", format, "--pokemon", str(pokemon)[:200],
                           "--output", "json"])
        try:
            return await asyncio.to_thread(mappers.map_detail, _raw_or_error(doc))
        except mappers.MappingError as e:
            # A panel name the dex snapshot can't resolve (meta refreshed ahead of the dex).
            # Surface a structured error instead of a bare 500 the UI can't distinguish.
            raise HTTPException(502, {"error": {"code": "not_found", "message": str(e)}})

    @app.get("/api/meta/trend")
    async def meta_trend(format: str = "single"):
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")

        def _load() -> Any:
            # Read + map off the event loop, and reuse the mapped DTO until the file's mtime
            # changes (a whole-season trend file is re-read + re-mapped on every request
            # otherwise, blocking the loop each time).
            current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
            season = current["current"]["season"]
            path = META_DATA / f"trend_{season}_{format}.json"
            if not path.exists():
                return None
            mtime = path.stat().st_mtime
            cached = trend_cache.get(format)
            if cached is None or cached[0] != mtime:
                cached = (mtime, mappers.map_trend(
                    json.loads(path.read_text(encoding="utf-8"))))
                trend_cache[format] = cached
            return cached[1]

        dto = await asyncio.to_thread(_load)
        if dto is None:
            raise HTTPException(404, f"no trend store for {format}")
        return dto

    @app.get("/api/meta/usage-trend")
    async def meta_usage_trend(format: str, pokemon: str):
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        slug = str(pokemon)[:200]

        def _load() -> Any:
            current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
            season = current["current"]["season"]
            path = META_DATA / f"usage_trend_{season}_{format}.json"
            if not path.exists():
                return None
            mtime = path.stat().st_mtime
            cached = usage_trend_cache.get(format)
            if cached is None or cached[0] != mtime:
                cached = (
                    mtime,
                    json.loads(path.read_text(encoding="utf-8")),
                    {},
                )
                usage_trend_cache[format] = cached
            if slug not in cached[1].get("pokemon", {}):
                return None
            if slug not in cached[2]:
                cached[2][slug] = mappers.map_usage_trend(cached[1], slug)
            return cached[2][slug]

        try:
            dto = await asyncio.to_thread(_load)
        except mappers.MappingError as exc:
            raise HTTPException(502, {"error": {"code": "not_found",
                                                "message": str(exc)}}) from exc
        if dto is None:
            raise HTTPException(404, f"no usage trend for {pokemon} in {format}")
        return dto

    @app.get("/api/meta/ko-trend")
    async def meta_ko_trend(format: str, pokemon: str):
        """History for the KO panels. Its own store because the KO axis is captured on its own
        clock — a single period list cannot describe both axes truthfully."""
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        slug = str(pokemon)[:200]

        def _load() -> Any:
            current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
            season = current["current"]["season"]
            path = META_DATA / f"ko_trend_{season}_{format}.json"
            if not path.exists():
                return None
            mtime = path.stat().st_mtime
            cached = ko_trend_cache.get(format)
            if cached is None or cached[0] != mtime:
                cached = (mtime, json.loads(path.read_text(encoding="utf-8")), {})
                ko_trend_cache[format] = cached
            if slug not in cached[1].get("pokemon", {}):
                return None
            if slug not in cached[2]:
                cached[2][slug] = mappers.map_ko_trend(cached[1], slug)
            return cached[2][slug]

        try:
            dto = await asyncio.to_thread(_load)
        except mappers.MappingError as exc:
            raise HTTPException(502, {"error": {"code": "not_found",
                                                "message": str(exc)}}) from exc
        if dto is None:
            raise HTTPException(404, f"no KO trend for {pokemon} in {format}")
        return dto

    @app.get("/api/meta/ko")
    async def meta_ko(format: str, pokemon: str):
        """The KO axis for one Pokemon. A SEPARATE file family from ranking/details with its own
        snapshot clock, so it is its own endpoint rather than another panel on /meta/detail."""
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        slug = str(pokemon)[:200]

        def _load() -> Any:
            current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
            season = current["current"]["season"]
            path = META_DATA / f"ko_{season}_{format}.json"
            if not path.exists():
                return None
            mtime = path.stat().st_mtime
            cached = ko_cache.get(format)
            if cached is None or cached[0] != mtime:
                doc = json.loads(path.read_text(encoding="utf-8"))
                by_slug = {r.get("slug") or mappers.slugify(r["pokemon_en"]): r
                           for r in doc.get("rows") or []}
                cached = (mtime, doc, by_slug, {})
                ko_cache[format] = cached
            row = cached[2].get(slug)
            if row is None:
                return None
            if slug not in cached[3]:
                cached[3][slug] = mappers.map_ko(cached[1], row, _usage_ranks(season, format))
            return cached[3][slug]

        try:
            dto = await asyncio.to_thread(_load)
        except mappers.MappingError as exc:
            raise HTTPException(502, {"error": {"code": "not_found",
                                                "message": str(exc)}}) from exc
        if dto is None:
            raise HTTPException(404, f"no KO data for {pokemon} in {format}")
        return dto

    def _top_meta(format: str) -> dict[str, Any] | None:
        """Both Top Meta scopes for one format, recomputed only when the shipped table or the in-game
        snapshot it is compared with changes. The comparison itself is the team skill's."""
        current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))["current"]
        season, rule = current["season"], current["rule"]
        table = mappers._topmeta_mod().table_path(format, rule)
        if not table.exists():
            return None
        inputs = (table, META_DATA / f"ranking_{season}_{format}.json",
                  META_DATA / f"details_{season}_{format}.json")
        stamp = tuple(p.stat().st_mtime if p.exists() else None for p in inputs)
        cached = top_meta_cache.get(format)
        if cached is None or cached[0] != stamp:
            docs = mappers.top_meta_docs(format, META_DATA, season, rule)
            cached = (stamp, {"docs": docs, "ranking": {}, "detail": {}})
            top_meta_cache[format] = cached
        return cached[1] if cached[1]["docs"] is not None else None

    @app.get("/api/meta/top-ranking")
    async def meta_top_ranking(format: str, scope: str = "season"):
        if format not in ("single", "double") or scope not in ("season", "rule"):
            raise HTTPException(400, "format must be single|double and scope season|rule")

        def _load() -> Any:
            state = _top_meta(format)
            if state is None:
                return None
            if scope not in state["ranking"]:
                state["ranking"][scope] = mappers.map_top_ranking(
                    state["docs"][scope], state["docs"]["slug_of"])
            return state["ranking"][scope]

        try:
            dto = await asyncio.to_thread(_load)
        except mappers.MappingError as exc:
            raise HTTPException(502, {"error": {"code": "not_found", "message": str(exc)}}) from exc
        if dto is None:
            raise HTTPException(404, f"no Top Meta table for {format}")
        return dto

    @app.get("/api/meta/top-detail")
    async def meta_top_detail(format: str, pokemon: str):
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        slug = str(pokemon)[:200]

        def _load() -> Any:
            state = _top_meta(format)
            if state is None:
                return None
            name = state["docs"]["by_slug"].get(slug)
            if name is None:
                return None
            if slug not in state["detail"]:
                docs = state["docs"]
                state["detail"][slug] = mappers.map_top_detail(
                    docs["season"], docs["rule"], name, docs["slug_of"])
            return state["detail"][slug]

        try:
            dto = await asyncio.to_thread(_load)
        except mappers.MappingError as exc:
            raise HTTPException(502, {"error": {"code": "not_found", "message": str(exc)}}) from exc
        if dto is None:
            raise HTTPException(404, f"no Top Meta data for {pokemon} in {format}")
        return dto

    @app.get("/api/meta/facets")
    async def meta_facets(format: str):
        """Inverted index for the filter rail. Built once per format and cached on the shipped
        files' mtimes: the per-pokemon detail files are lazy, so answering "which mons run Choice
        Scarf" from the client would otherwise cost one fetch per roster entry."""
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")

        def _load() -> Any:
            current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
            season = current["current"]["season"]
            rule = current["current"]["rule"]
            details_path = META_DATA / f"details_{season}_{format}.json"
            ko_path = META_DATA / f"ko_{season}_{format}.json"
            if not details_path.exists():
                return None
            stamp = (details_path.stat().st_mtime,
                     ko_path.stat().st_mtime if ko_path.exists() else 0.0)
            cached = facets_cache.get(format)
            if cached is None or cached[0] != stamp:
                details = json.loads(details_path.read_text(encoding="utf-8")).get("rows") or []
                ko_rows = (json.loads(ko_path.read_text(encoding="utf-8")).get("rows") or []
                           if ko_path.exists() else [])
                dto = {"season": season, "rule": rule, "format": format,
                       "facets": mappers.build_facets(details, ko_rows)}
                cached = (stamp, dto)
                facets_cache[format] = cached
            return cached[1]

        dto = await asyncio.to_thread(_load)
        if dto is None:
            raise HTTPException(404, f"no metagame data for {format}")
        return dto

    # -- calc --------------------------------------------------------------------------

    # Forwarded verbatim to the calc CLI; anything else in the body is dropped. `switch_in_drops`
    # belongs here because a tuning frame turns it off to stay history-free, and silently dropping it
    # made the in-page quick solve disagree with the `tune` operator on every Intimidate defender.
    CALC_REQUEST_KEYS = ("attacker", "defender", "move", "field", "switch_in_drops")

    @app.post("/api/calc/damage")
    async def calc_damage(body: dict):
        payload = json.dumps({k: body[k] for k in CALC_REQUEST_KEYS if k in body},
                             ensure_ascii=False)
        doc = await query("calc", ["one"], payload)
        return mappers.map_damage(_raw_or_error(doc))

    @app.post("/api/calc/speedline")
    async def calc_speedline(body: dict):
        doc = await query("speedline", ["one"], json.dumps(body, ensure_ascii=False))
        return mappers.map_speedline(_raw_or_error(doc))

    @app.post("/api/calc/batch")
    async def calc_batch(body: dict):
        # One attacker's several moves × several defenders, dispatched as ONE fault-isolated batch
        # (the calc CLI's `batch` command reuses a single loaded engine) instead of N×M requests.
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise HTTPException(400, "items required")
        if len(items) > 240:
            raise HTTPException(413, "too many items")
        # Keep the input→result position contract: a non-object item can't be sent to the engine, so
        # it gets a bad_input error IN ITS OWN SLOT instead of being dropped (which would shift every
        # later result onto the wrong request cell — external audit 2026-07-14).
        valid = [(i, {k: it[k] for k in CALC_REQUEST_KEYS if k in it})
                 for i, it in enumerate(items) if isinstance(it, dict)]
        doc = await query("calc", ["batch"], json.dumps([v[1] for v in valid], ensure_ascii=False))
        if not isinstance(doc, list) or len(doc) != len(valid):
            raise HTTPException(502, {"error": {"code": "bad_input",
                                     "message": "calc batch produced a mismatched array"}})
        # Each engine result is a success (mapped) or the uniform error shape (one bad cell never
        # fails the grid); bad_input fills the slots that never reached the engine.
        out: list[Any] = [_BAD_INPUT_ITEM for _ in items]
        for (orig_i, _), res in zip(valid, doc):
            out[orig_i] = res if (isinstance(res, dict) and res.get("error")) \
                else mappers.map_damage(res)
        return out

    @app.post("/api/calc/speedbatch")
    async def calc_speedbatch(body: dict):
        # A whole speed ladder (my mon + opponents) resolved in ONE fault-isolated speedline batch
        # (the speed tool's tier list + the tune tool never fire per-mon round-trips).
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise HTTPException(400, "items required")
        if len(items) > 240:
            raise HTTPException(413, "too many items")
        keys = ("name", "nature", "sps", "ivs", "boosts", "ability", "item", "status", "field")
        # Same position contract as /calc/batch: bad items get bad_input in place, never dropped.
        valid = [(i, {k: it[k] for k in keys if k in it})
                 for i, it in enumerate(items) if isinstance(it, dict)]
        doc = await query("speedline", ["batch"], json.dumps([v[1] for v in valid], ensure_ascii=False))
        if not isinstance(doc, list) or len(doc) != len(valid):
            raise HTTPException(502, {"error": {"code": "bad_input",
                                     "message": "speedline batch produced a mismatched array"}})
        out: list[Any] = [_BAD_INPUT_ITEM for _ in items]
        for (orig_i, _), res in zip(valid, doc):
            out[orig_i] = res if (isinstance(res, dict) and res.get("error")) \
                else mappers.map_speedline(res)
        return out

    # -- team: opponent matchup cache (KO matrix + derived check grid) -------------------

    @app.get("/api/team/oppcache")
    async def team_oppcache(format: str = "single", view: str = "matrix", species: str | None = None):
        if format not in ("single", "double"):
            raise HTTPException(400, "format must be single|double")
        if view not in ("matrix", "ko", "checks", "sets", "details"):
            raise HTTPException(400, "view must be matrix|ko|checks|sets|details")
        if view == "details" and not species:
            raise HTTPException(400, "species is required for details")

        def _load() -> Any:
            # Read + map off the event loop; reuse the mapped DTO until the cache file's mtime
            # changes (mapping the whole grid — and deriving checks — is not cheap to repeat).
            current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
            rule = current["current"]["rule"]        # the opponent cache is keyed by RULE
            path = META_DATA.parent.parent / "pokemon-champions-team" / "data" \
                / "opponent_cache" / f"{rule}_{format}.json"
            if not path.exists():
                return None
            key = f"{format}:{'matrix' if view == 'details' else view}"
            mtime = path.stat().st_mtime
            cached = oppcache_cache.get(key)
            expiry = cached[1].get("teamEvidenceExpiresAt") if cached else None
            expired = expiry is not None and datetime.now(timezone.utc) >= datetime.fromisoformat(expiry)
            if cached is None or cached[0] != mtime or expired:
                cache = mappers.load_oppcache(format, rule)
                if view == "checks":
                    dto = mappers.map_oppcheck_grid(cache, mappers.derive_oppcheck_grid(cache))
                elif view == "ko":
                    dto = mappers.map_oppko_grid(cache)
                elif view == "sets":
                    dto = mappers.map_oppsets(cache)
                else:
                    dto = mappers.map_oppcache(cache)
                cached = (mtime, dto)
                oppcache_cache[key] = cached
            return mappers.map_oppdetails(cached[1], species) if view == "details" else cached[1]

        try:
            dto = await asyncio.to_thread(_load)
        except (RuntimeError, ValueError) as exc:
            # StaleCacheError is a RuntimeError; an older cache without check_matrix fails
            # derivation with ValueError. Both are deploy/update-state problems with the same
            # operator action, not opaque application crashes.
            if "opponent cache" not in str(exc):
                raise
            raise HTTPException(503, str(exc)) from exc
        if dto is None:
            raise HTTPException(404, f"no opponent cache for {format}")
        return dto

    # -- team (batched session, raw passthrough in P0) ----------------------------------

    @app.post("/api/team/session")
    async def team_session(body: dict):
        ops = body.get("ops")
        if not isinstance(ops, list) or not ops:
            raise HTTPException(400, "ops required")
        session_id = body.get("session")
        run_store = store
        revision = None
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id:
                raise HTTPException(400, "session must be a session id")
            try:
                revision = run_store.get_session(session_id)["revision"]
            except NotFound:
                raise HTTPException(404, "session not found")
            if activity.get(session_id, {}).get("state") == "running":
                raise HTTPException(409, "an operator is already running for this session")
        for op in ops:
            if not isinstance(op, dict) or not isinstance(op.get("op"), str) or op["op"] not in TEAM_OPS:
                raise HTTPException(400, "op not allowed")
        label = " + ".join(str(op["op"]) for op in ops)
        run = {"op": label, "state": "running", "startedAt": _iso(time.time()), "started": time.time()}
        lease = object()
        active_runs.add(lease)
        ok = False
        try:
            if session_id:
                activity[session_id] = run
                publish("activity", {"sessionId": session_id})
            import tempfile
            inputs: list[dict[str, bytes]] = []
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                spec_ops: list[dict] = []
                for i, op in enumerate(ops):
                    out = dict(op)
                    materialized: dict[str, bytes] = {}
                    for field in TEAM_FILE_FIELDS:
                        value = out.get(field)
                        if value is None:
                            continue
                        if not isinstance(value, (dict, list)):
                            raise HTTPException(400, f"ops[{i}].{field} must be inline JSON, not a path")
                        path = tmp_dir / f"op{i}-{field}.json"
                        materialized[field] = json.dumps(value, ensure_ascii=False).encode("utf-8")
                        path.write_bytes(materialized[field])
                        out[field] = str(path)
                    spec_ops.append(out)
                    inputs.append(materialized)
                spec = tmp_dir / "session.json"
                spec.write_text(json.dumps(spec_ops, ensure_ascii=False), encoding="utf-8")
                doc = await asyncio.to_thread(
                    pool.request_json, "team", ["session", str(spec)], None, TEAM_TIMEOUT)
            if not isinstance(doc, list) or len(doc) != len(ops):
                raise HTTPException(502, "operator returned an incomplete batch")
            if session_id:
                groups = [gate_artifacts(op, materialized, row) for op, materialized, row in zip(ops, inputs, doc)]
                events = iter(run_store.append_artifacts(
                    session_id, [pair for group in groups for pair in group], revision, deduplicate=True))
                for group, row in zip(groups, doc):
                    appended = [event for _ in group if (event := next(events, None)) is not None]
                    if appended and isinstance(row, dict):
                        row["artifacts"] = [{key: event[key] for key in ("kind", "artifactHash", "revision")}
                                            for event in appended]
            ok = all(isinstance(row, dict) and row.get("error") is None and row.get("result") is not None for row in doc)
            return doc
        except NotFound as exc:
            raise HTTPException(404, "session not found") from exc
        except Conflict as exc:
            raise HTTPException(409, {"expected": exc.expected, "actual": exc.actual,
                                      "message": "session changed while the operator ran; re-read before running again"}) from exc
        finally:
            active_runs.discard(lease)
            # A deleted session has already removed its activity; never resurrect it or read it by id.
            if session_id and activity.get(session_id) is run:
                if ok:
                    activity.pop(session_id, None)
                else:
                    run.update(state="failed", ended=time.time())
                publish("activity", {"sessionId": session_id})

    @app.post("/api/team/code")
    async def team_code_import(body: dict):
        from .team_code import resolve
        return await asyncio.to_thread(resolve, body)

    @app.post("/api/team/matchup")
    async def actual_team_matchup(body: dict):
        """Actual registered sets versus a caller-selected top-K (1..60).

        This narrower endpoint exists alongside the developer-oriented session surface so both
        runtimes expose one validated UI contract, including free-form text parsing.
        """
        try:
            return await asyncio.to_thread(run_actual_matchup, pool, body)
        except MatchupInputError as exc:
            raise HTTPException(400, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, "actual matchup calculation failed") from exc

    @app.post("/api/team/tune")
    async def team_tune(body: dict):
        """Authoritative SP cliff calculation over a path-free team + benchmark request."""
        try:
            return await asyncio.to_thread(run_team_tune, pool, body)
        except TuneInputError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, "team tune calculation failed") from exc

    # -- sessions / artifacts ------------------------------------------------------------

    @app.post("/api/sessions")
    async def create_session(body: dict | None = None):
        try:
            session = store.create_session((body or {}).get("meta"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        publish("session", {"sessionId": session["id"], "change": "created"})
        return session

    @app.get("/api/sessions")
    async def list_sessions(limit: int | None = None, prioritize_decisions: bool = False,
                            archived: str = "exclude"):
        if limit is not None and not 1 <= limit <= 50:
            raise HTTPException(400, "limit must be between 1 and 50")
        if archived not in ("exclude", "include", "only"):
            raise HTTPException(400, "archived must be exclude, include or only")
        return [live_view(view) for view in store.list_session_views(limit, prioritize_decisions, archived)]

    @app.get("/api/sessions/overview")
    async def session_overview(team_id: str | None = None):
        overview = store.session_overview(team_id)
        overview["sessions"] = [live_view(view) for view in overview["sessions"]]
        return overview

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        try:
            out = store.get_session_view(session_id)
        except NotFound:
            raise HTTPException(404, "session not found")
        return live_view(out)

    @app.post("/api/sessions/{session_id}/title")
    async def rename_session(session_id: str, body: dict):
        title = body.get("title")
        if title is not None and (not isinstance(title, str) or len(title) > 200):
            raise HTTPException(400, "title must be a string of at most 200 characters")
        try:
            store.set_title(session_id, title)
        except NotFound:
            raise HTTPException(404, "session not found")
        publish("session", {"sessionId": session_id, "change": "updated"})
        return live_view(store.get_session_view(session_id))

    @app.post("/api/sessions/{session_id}/archive")
    async def archive_session(session_id: str, body: dict):
        if not isinstance(body.get("archived"), bool):
            raise HTTPException(400, "archived (bool) required")
        try:
            store.set_archived(session_id, body["archived"])
        except NotFound:
            raise HTTPException(404, "session not found")
        publish("session", {"sessionId": session_id, "change": "updated"})
        return live_view(store.get_session_view(session_id))

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        try:
            store.delete_session(session_id)
        except NotFound:
            raise HTTPException(404, "session not found")
        activity.pop(session_id, None)
        waiters.pop(session_id, None)
        picked.pop(session_id, None)
        publish("session", {"sessionId": session_id, "change": "deleted"})
        return {"ok": True}

    diagnosis_reports: OrderedDict[tuple[str, str], dict] = OrderedDict()

    @app.get("/api/sessions/{session_id}/diagnose")
    async def session_diagnosis(session_id: str):
        """The session's latest diagnosis as the same report the online diagnose page renders:
        the operator's facts, the team it read, and that team's legality checked now."""
        try:
            source_store = store
            pair = source_store.artifact_pair(session_id, "diagnose", "team")
        except NotFound:
            raise HTTPException(404, "session not found")
        if pair is None:
            raise HTTPException(404, "this session has no diagnosis")
        try:
            raw = json.loads(pair["output"]["raw_payload"])
            team = json.loads(pair["input"]["raw_payload"]) if pair["input"] else pair["meta"].get("team")
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "the diagnosis artifact is not readable") from exc
        if not isinstance(raw, dict) or not isinstance(team, dict):
            raise HTTPException(422, "the diagnosis has no team to report on")
        team_hash = hashlib.sha256(json.dumps(team, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = (pair["output"]["hash"], team_hash)
        if key not in diagnosis_reports:
            legality = await asyncio.to_thread(validate_team, pool, team)
            if source_store is not store:
                raise HTTPException(409, "workspace changed while validating the diagnosis")
            diagnosis_reports[key] = mappers.map_diagnose_report(team, legality, raw)
        report = diagnosis_reports[key]
        diagnosis_reports.move_to_end(key)
        while len(diagnosis_reports) > 32:
            diagnosis_reports.popitem(last=False)
        return report

    @app.get("/api/sessions/{session_id}/wait")
    async def wait_for_reader(session_id: str, kinds: str = ",".join(PANEL_KINDS), after: int = 0,
                              timeout: float = WAIT_POLL_SECONDS):
        """Long-poll for the reader's next move: return once an artifact of `kinds` has sequence
        >= `after`, or `timeout` after up to one poll. While an assistant polls, its session shows
        it waiting, so the panel can say the assistant will continue on its own."""
        wanted = [kind for kind in kinds.split(",") if kind]
        if not wanted or any(kind not in PANEL_KINDS for kind in wanted):
            raise HTTPException(400, "kinds must be decision and/or intake-answer")
        if not math.isfinite(timeout):
            raise HTTPException(400, "timeout must be finite")
        timeout = max(1.0, min(float(timeout), WAIT_POLL_SECONDS))
        source_store = store

        def ready() -> dict | None:
            if source_store is not store:
                raise HTTPException(409, "workspace changed while waiting")
            store.get_session(session_id)
            rows = store.changes_since_revision(session_id, after)
            return next((row for row in reversed(rows) if row["kind"] in wanted), None)

        def settle(hit: dict | None):
            if hit:
                picked[session_id] = max(picked.get(session_id, -1), hit["sequence"])
                waiters.pop(session_id, None)
                publish("activity", {"sessionId": session_id})
            return {"status": "ready", **hit} if hit else {"status": "timeout"}

        try:
            hit = ready()
        except NotFound:
            raise HTTPException(404, "session not found")
        if hit:
            return settle(hit)
        now = time.time()
        previous = waiters.get(session_id, {})
        announce = previous.get("until", 0) <= now or now - previous.get("notified", 0) >= 10
        waiters[session_id] = {"kinds": wanted, "until": now + timeout + WAIT_GRACE_SECONDS,
                               "notified": now if announce else previous["notified"]}
        if announce:
            publish("activity", {"sessionId": session_id})
        loop_ref.setdefault("loop", asyncio.get_running_loop())
        waker = asyncio.Event()
        wakers.setdefault(session_id, set()).add(waker)
        try:
            await asyncio.wait_for(waker.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            wakers.get(session_id, set()).discard(waker)
        try:
            return settle(ready())
        except NotFound:
            raise HTTPException(404, "session not found")

    @app.post("/api/sessions/{session_id}/artifacts")
    async def append_artifact(session_id: str, body: dict):
        kind = str(body.get("kind", ""))
        payload = body.get("payload")
        revision = body.get("revision")
        if not kind or not isinstance(payload, str) or not isinstance(revision, int):
            raise HTTPException(400, "kind, payload (string), revision (int) required")
        try:
            return store.append_artifact(session_id, kind, payload.encode("utf-8"), revision)
        except NotFound:
            raise HTTPException(404, "session not found")
        except Conflict as c:
            changes = store.changes_since_revision(session_id, c.expected)
            raise HTTPException(409, {"expected": c.expected, "actual": c.actual,
                                      "changedKinds": list(dict.fromkeys(
                                          row["kind"] for row in changes)),
                                      "changes": changes,
                                      "session": store.get_session(session_id)})

    @app.get("/api/artifacts/{artifact_hash}")
    async def get_artifact(artifact_hash: str):
        try:
            a = store.get_artifact(artifact_hash)
        except NotFound:
            raise HTTPException(404, "artifact not found")
        return Response(content=a["payload"], media_type="application/json",
                        headers={"X-Artifact-Kind": a["kind"]})

    # -- SSE -----------------------------------------------------------------------------

    @app.get("/api/events")
    async def events():
        loop_ref["loop"] = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        subscribers.add(q)

        async def stream():
            try:
                yield f"retry: {SSE_RETRY_MS}\nevent: hello\ndata: {{}}\n\n"
                while True:
                    try:
                        name, event = await asyncio.wait_for(q.get(), SSE_PING_SECONDS)
                    except asyncio.TimeoutError:
                        # Named so the page sees it: a stream that stops pinging is dead even when
                        # the browser has not noticed, and the page reconnects and re-reads.
                        yield "event: ping\ndata: {}\n\n"
                        continue
                    head = f"event: {name}\n" if name else ""
                    yield f"{head}data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                subscribers.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    install_openapi(app, "local")

    # Static hosting (mounted last — API routes above resolve first): the projection data
    # layer and the built SPA. A packaged release's shared index names its immutable JS/CSS
    # through the production-scoped URL, so the local release verifier must expose that asset
    # path too even though local projection reads deliberately remain at /projection.
    # Optional so a headless/API-only bridge still runs.
    if deployment_id and dist_dir is not None and (dist_dir / "assets").is_dir():
        app.mount(
            f"/releases/{deployment_id}/dist/assets",
            StaticFiles(directory=str(dist_dir / "assets")),
            name="release-assets",
        )
    if projection_dir is not None and projection_dir.is_dir():
        app.mount("/projection", StaticFiles(directory=str(projection_dir)), name="projection")
    else:
        # No projection mounted: without this, /projection/*.json would fall through to the
        # SPA '/' mount and get index.html with a 200, so the client's fetchJson sees
        # ok=true and dies on JSON.parse('<!doctype html>') — an opaque error that hides the
        # real cause (projection was never built). A real 404 makes the failure legible.
        @app.get("/projection/{_path:path}")
        async def projection_missing(_path: str):
            raise HTTPException(404, "projection not built")

    if dist_dir is not None and dist_dir.is_dir():
        app.mount("/", _SpaStaticFiles(directory=str(dist_dir), html=True), name="spa")

    return app
