"""Formal OpenAPI assembly over the generated Web protocol schema catalog.

FastAPI's handlers intentionally accept plain ``dict`` at the trust boundary, so its automatic
schema would describe many request and response bodies as empty objects.  This module keeps one
explicit route catalog per runtime, grafts the Zod-generated schemas into OpenAPI 3.1 components,
and fails if a public ``/api`` route is missing from the catalog.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

Surface = Literal["local", "online"]
RouteKey = tuple[str, str]
Schema = dict[str, Any]

OPENAPI_PATH = "/api/openapi.json"
OPENAPI_VERSION = "3.1.0"


def _ref(name: str) -> Schema:
    return {"$ref": f"#/components/schemas/{name}"}


def _array(items: Schema) -> Schema:
    return {"type": "array", "items": items}


def _object(*, properties: dict[str, Schema] | None = None,
            required: list[str] | None = None,
            additional: bool | Schema = False) -> Schema:
    out: Schema = {"type": "object", "additionalProperties": additional}
    if properties:
        out["properties"] = properties
    if required:
        out["required"] = required
    return out


def _route(summary: str, tag: str, response: Schema, *,
           request: Schema | None = None, request_required: bool = True,
           response_content_type: str = "application/json",
           extra_content: dict[str, Schema] | None = None,
           response_headers: dict[str, Any] | None = None,
           security: list[dict[str, list[str]]] | None = None) -> dict[str, Any]:
    content = {response_content_type: {"schema": response}}
    for media_type, schema in (extra_content or {}).items():
        content[media_type] = {"schema": schema}
    operation: dict[str, Any] = {
        "summary": summary,
        "tags": [tag],
        "responses": {
            "200": {
                "description": "Successful response",
                "content": content,
            },
        },
    }
    if response_headers:
        operation["responses"]["200"]["headers"] = response_headers
    if security is not None:
        operation["security"] = security
    if request is not None:
        operation["requestBody"] = {
            "required": request_required,
            "content": {"application/json": {"schema": request}},
        }
    return operation


_HEALTH = _object(properties={"ok": {"type": "boolean"}}, required=["ok"])
_BOOTSTRAP = _object(
    properties={"token": {"type": "string", "minLength": 1}}, required=["token"])
_DEX_QUERY = _object(
    properties={"name": {"type": "string", "minLength": 1, "maxLength": 200}},
    required=["name"])
_DEX_RESULT = {
    "oneOf": [_ref(name) for name in (
        "PokemonCardDtoSchema", "MoveDtoSchema", "ItemDtoSchema",
        "AbilityDtoSchema", "NatureDtoSchema",
    )],
}
_RESOLVE_BATCH = _object(
    properties={
        "names": {"type": "array", "minItems": 1, "maxItems": 100,
                  "items": {"type": "string", "minLength": 1, "maxLength": 200}},
        "kind": {"type": "string",
                 "enum": ["pokemon", "move", "item", "ability", "nature"]},
    },
    required=["names"],
)
_TEAM_SESSION = _object(
    properties={
        "session": {"type": "string", "minLength": 1,
                    "description": "Submit the gate operators' artifacts to this session"},
        "ops": {
            "type": "array",
            "minItems": 1,
            "items": _object(
                properties={"op": {"type": "string", "minLength": 1}},
                required=["op"],
                additional=True,
            ),
        },
    },
    required=["ops"],
)
_TEAM_SESSION_RESULT = _array(_object(additional=True))
_SESSION_CREATE = _object(
    properties={"meta": _object(additional=True)},
    additional=False,
)
_ARTIFACT_APPEND = _object(
    properties={
        "kind": {"type": "string", "minLength": 1},
        "payload": {"type": "string"},
        "revision": {"type": "integer", "minimum": 0},
    },
    required=["kind", "payload", "revision"],
)
_SSE = {"type": "string", "description": "Server-sent event stream"}
_NDJSON = {"type": "string", "description": "Newline-delimited JSON event stream"}


LOCAL_ROUTES: dict[RouteKey, dict[str, Any]] = {
    ("GET", "/api/workspace"): _route("Read workspace location", "workspace", _ref("WorkspaceInfoDtoSchema")),
    ("POST", "/api/workspace/pick-folder"): _route("Choose a local workspace folder", "workspace", _ref("WorkspacePickerResultDtoSchema"), request=_ref("WorkspacePickerRequestDtoSchema")),
    ("POST", "/api/workspace/import-browser"): _route("Import old browser data into an empty workspace", "workspace", _HEALTH, request=_ref("WorkspaceBrowserImportDtoSchema")),
    ("POST", "/api/workspace/switch"): _route("Switch user workspace", "workspace", _ref("WorkspaceInfoDtoSchema"), request=_ref("WorkspaceSwitchRequestDtoSchema")),
    ("GET", "/api/workspace/library"): _route("Read user library", "workspace", _ref("WorkspaceLibraryDtoSchema")),
    ("POST", "/api/workspace/library"): _route("Atomically save user library", "workspace", _ref("WorkspaceLibraryDtoSchema"), request=_ref("WorkspaceLibraryDtoSchema")),
    ("GET", "/api/workspace/records"): _route("Read saved results", "workspace", _ref("WorkspaceRecordsDtoSchema")),
    ("POST", "/api/workspace/records"): _route("Save one result", "workspace", _HEALTH, request=_ref("WorkspaceRecordWriteDtoSchema")),
    ("POST", "/api/workspace/records/delete"): _route("Delete saved results", "workspace", _HEALTH, request=_ref("WorkspaceRecordDeleteDtoSchema")),
    ("GET", "/api/health"): _route("Bridge liveness", "system", _HEALTH),
    ("POST", "/api/bootstrap"): _route(
        "Exchange one-time bootstrap token", "system", _HEALTH, request=_BOOTSTRAP,
        security=[]),
    ("POST", "/api/bootstrap/mint"): _route(
        "Mint another one-time bootstrap token (CLI secret only)", "system", _BOOTSTRAP,
        request=_object(), request_required=False, security=[{"PcuiSecret": []}]),
    ("GET", "/api/console"): _route(
        "Read the local Console's assistant and projection status", "system",
        _ref("ConsoleStatusDtoSchema")),
    ("GET", "/api/capabilities"): _route(
        "Read runtime capabilities", "system", _ref("CapabilitiesSchema")),
    ("POST", "/api/dex/{kind}"): _route(
        "Look up one dex entity", "dex", _DEX_RESULT, request=_DEX_QUERY),
    ("POST", "/api/dex/resolve/batch"): _route(
        "Resolve canonical entity names", "dex", _array(_ref("ResolveEntryDtoSchema")),
        request=_RESOLVE_BATCH),
    ("GET", "/api/dex/learners"): _route(
        "Read the move -> learners index", "dex", _ref("LearnersDtoSchema")),
    ("GET", "/api/meta/ranking"): _route(
        "Read metagame ranking", "meta", _ref("RankingDtoSchema")),
    ("GET", "/api/meta/detail"): _route(
        "Read one metagame detail panel", "meta", _ref("MetaDetailDtoSchema")),
    ("GET", "/api/meta/trend"): _route(
        "Read rank history", "meta", _ref("TrendDtoSchema")),
    ("GET", "/api/meta/usage-trend"): _route(
        "Read one Pokemon's panel usage history", "meta", _ref("UsageTrendDtoSchema")),
    ("GET", "/api/meta/ko"): _route(
        "Read one Pokemon's KO axis", "meta", _ref("MetaKoDtoSchema")),
    ("GET", "/api/meta/ko-trend"): _route(
        "Read one Pokemon's KO panel history", "meta", _ref("MetaKoTrendDtoSchema")),
    ("GET", "/api/meta/facets"): _route(
        "Read the filter facet index", "meta", _ref("MetaFacetsDtoSchema")),
    ("GET", "/api/meta/top-ranking"): _route(
        "Read one Top Meta ranking scope", "meta", _ref("TopMetaRankingDtoSchema")),
    ("GET", "/api/meta/top-detail"): _route(
        "Read one Pokemon's Top Meta panels", "meta", _ref("TopMetaDetailDtoSchema")),
    ("POST", "/api/calc/damage"): _route(
        "Calculate one damage matchup", "calc", _ref("DamageResultDtoSchema"),
        request=_ref("DamageRequestDtoSchema")),
    ("POST", "/api/calc/speedline"): _route(
        "Calculate one speed line", "calc", _ref("SpeedlineDtoSchema"),
        request=_ref("SpeedInputDtoSchema")),
    ("POST", "/api/calc/batch"): _route(
        "Calculate a damage batch", "calc", _ref("DamageBatchResultDtoSchema"),
        request=_ref("DamageBatchRequestDtoSchema")),
    ("POST", "/api/calc/speedbatch"): _route(
        "Calculate a speed batch", "calc", _ref("SpeedBatchResultDtoSchema"),
        request=_ref("SpeedBatchRequestDtoSchema")),
    ("GET", "/api/team/oppcache"): _route(
        "Read opponent matchup projection", "team", {
            "oneOf": [_ref("OppCacheDtoSchema"), _ref("OppKoGridDtoSchema"),
                      _ref("OppCheckGridDtoSchema"), _ref("OppSetCatalogDtoSchema"),
                      _ref("OppDetailsDtoSchema")],
        }),
    ("POST", "/api/team/session"): _route(
        "Run a deterministic team operator batch", "team", _TEAM_SESSION_RESULT,
        request=_TEAM_SESSION),
    ("POST", "/api/team/matchup"): _route(
        "Calculate registered-team matchup facts", "team",
        _ref("ActualMatchupResponseDtoSchema"),
        request=_ref("ActualMatchupRequestDtoSchema")),
    ("POST", "/api/team/tune"): _route(
        "Calculate authoritative SP cliffs", "team", _ref("TuneResultDtoSchema"),
        request=_ref("TuneRequestDtoSchema")),
    ("POST", "/api/sessions"): _route(
        "Create a UEP session", "sessions", _ref("SessionDtoSchema"),
        request=_SESSION_CREATE, request_required=False),
    ("GET", "/api/sessions"): _route(
        "List UEP sessions", "sessions", _array(_ref("SessionDtoSchema"))),
    ("GET", "/api/sessions/overview"): _route(
        "Read bounded Console home cards and session counts", "sessions", _ref("SessionOverviewDtoSchema")),
    ("GET", "/api/sessions/{session_id}"): _route(
        "Read a UEP session and ledger", "sessions", _ref("SessionWithLedgerDtoSchema")),
    ("POST", "/api/sessions/{session_id}/artifacts"): _route(
        "Append a content-addressed artifact", "sessions",
        _ref("ArtifactAppendEventDtoSchema"), request=_ARTIFACT_APPEND),
    ("POST", "/api/sessions/{session_id}/title"): _route(
        "Set or clear a session's display title", "sessions", _ref("SessionDtoSchema"),
        request=_ref("SessionTitleRequestDtoSchema")),
    ("POST", "/api/sessions/{session_id}/archive"): _route(
        "Archive or restore a session", "sessions", _ref("SessionDtoSchema"),
        request=_ref("SessionArchiveRequestDtoSchema")),
    ("DELETE", "/api/sessions/{session_id}"): _route(
        "Delete a session and its ledger", "sessions", _HEALTH),
    ("GET", "/api/sessions/{session_id}/diagnose"): _route(
        "Read a session's latest diagnosis as a report", "sessions", _ref("DiagnoseFactsDtoSchema")),
    ("GET", "/api/sessions/{session_id}/wait"): _route(
        "Long-poll for the reader's next panel artifact", "sessions",
        _ref("SessionWaitResultDtoSchema")),
    ("GET", "/api/artifacts/{artifact_hash}"): _route(
        "Read an artifact payload", "sessions", {},
        response_headers={
            "X-Artifact-Kind": {
                "description": "Stored artifact kind",
                "schema": {"type": "string"},
            },
        }),
    ("GET", "/api/events"): _route(
        "Subscribe to session artifact events", "sessions", _SSE,
        response_content_type="text/event-stream"),
}


ONLINE_ROUTES: dict[RouteKey, dict[str, Any]] = {
    ("GET", "/api/health"): _route("Online API liveness", "system", _HEALTH),
    ("GET", "/api/capabilities"): _route(
        "Read runtime capabilities", "system", _ref("CapabilitiesSchema")),
    ("GET", "/api/quota"): _route(
        "Read anonymous daily quotas", "system", _ref("OnlineQuotaDtoSchema")),
    # Every slow online surface also answers as an NDJSON event stream when the client asks for
    # one (Accept: application/x-ndjson) — see the server's STREAM_HEARTBEAT.
    ("POST", "/api/qa"): _route(
        "Ask one deterministic-tool-grounded question", "llm", _ref("QaAnswerDtoSchema"),
        request=_ref("QaRequestDtoSchema"),
        extra_content={"application/x-ndjson": _NDJSON}),
    ("POST", "/api/team/diagnose"): _route(
        "Diagnose one team", "team", _ref("DiagnoseReportDtoSchema"),
        request=_ref("DiagnoseRequestDtoSchema"),
        extra_content={"application/x-ndjson": _NDJSON}),
    ("POST", "/api/team/matchup"): _route(
        "Calculate registered-team matchup facts", "team",
        _ref("ActualMatchupResponseDtoSchema"),
        request=_ref("ActualMatchupRequestDtoSchema"),
        extra_content={"application/x-ndjson": _NDJSON}),
    ("POST", "/api/team/tune"): _route(
        "Calculate authoritative SP cliffs", "team", _ref("TuneResultDtoSchema"),
        request=_ref("TuneRequestDtoSchema"),
        extra_content={"application/x-ndjson": _NDJSON}),
    ("POST", "/api/builder"): _route(
        "Start one simplified team build", "builder", _ref("BuilderStartDtoSchema"),
        request=_ref("BuilderRequestDtoSchema")),
    ("GET", "/api/builder/jobs/{job_id}"): _route(
        "Read one builder job", "builder", _ref("BuilderJobDtoSchema")),
    ("GET", "/api/builder/jobs/{job_id}/events"): _route(
        "Subscribe to one builder job", "builder", _SSE,
        response_content_type="text/event-stream"),
}


def route_catalog(surface: Surface) -> dict[RouteKey, dict[str, Any]]:
    return LOCAL_ROUTES if surface == "local" else ONLINE_ROUTES


def protocol_schema_path() -> Path:
    """Return the release-bundled catalog, or the monorepo source catalog in development."""
    bundled = Path(__file__).with_name("protocol.schema.json")
    if bundled.is_file():
        return bundled
    source = Path(__file__).resolve().parents[3] / "frontend/protocol/schema/protocol.schema.json"
    if source.is_file():
        return source
    raise RuntimeError("protocol JSON Schema catalog is missing")


def _rewrite_component_refs(value: Any) -> Any:
    if isinstance(value, list):
        return [_rewrite_component_refs(entry) for entry in value]
    if not isinstance(value, dict):
        return value
    out = {key: _rewrite_component_refs(entry) for key, entry in value.items()}
    ref = out.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        out["$ref"] = "#/components/schemas/" + ref.removeprefix("#/$defs/")
    return out


def protocol_components(path: Path | None = None) -> dict[str, Schema]:
    catalog = json.loads((path or protocol_schema_path()).read_text(encoding="utf-8"))
    definitions = catalog.get("$defs")
    if not isinstance(definitions, dict) or not definitions:
        raise RuntimeError("protocol JSON Schema catalog has no $defs")
    return {name: _rewrite_component_refs(schema)
            for name, schema in definitions.items()}


def api_routes(app: FastAPI) -> set[RouteKey]:
    return {
        (method.upper(), route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.include_in_schema
        and route.path.startswith("/api/")
        and route.path != OPENAPI_PATH
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }


def assert_route_catalog_coverage(app: FastAPI, surface: Surface) -> None:
    actual = api_routes(app)
    documented = set(route_catalog(surface))
    missing = sorted(actual - documented)
    stale = sorted(documented - actual)
    if missing or stale:
        raise RuntimeError(
            f"{surface} OpenAPI route catalog drift: missing={missing}, stale={stale}")


def _merge_operation(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if key == "responses":
            responses = merged.setdefault("responses", {})
            for status, response in value.items():
                responses[status] = copy.deepcopy(response)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def build_openapi(app: FastAPI, surface: Surface) -> dict[str, Any]:
    assert_route_catalog_coverage(app, surface)
    schema = get_openapi(
        title=app.title,
        version="4",
        openapi_version=OPENAPI_VERSION,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    # Keep FastAPI's own validation/error schemas for the generated 422 responses, then add the
    # protocol catalog. A replacement would leave dangling HTTPValidationError references.
    components["schemas"] = {
        **components.get("schemas", {}),
        **protocol_components(),
    }
    if surface == "local":
        components["securitySchemes"] = {
            "PcuiCookie": {"type": "apiKey", "in": "cookie", "name": "pcui_session"},
            "PcuiSecret": {"type": "apiKey", "in": "header", "name": "X-PCUI-Secret"},
        }
        schema["security"] = [{"PcuiCookie": []}, {"PcuiSecret": []}]

    for (method, path), operation in route_catalog(surface).items():
        base = schema["paths"][path][method.lower()]
        schema["paths"][path][method.lower()] = _merge_operation(base, operation)
    return schema


def install_openapi(app: FastAPI, surface: Surface) -> None:
    """Install cached schema generation and a non-interactive JSON endpoint."""
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app, surface)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.get(OPENAPI_PATH, include_in_schema=False)
    async def openapi_document() -> JSONResponse:
        return JSONResponse(custom_openapi())
