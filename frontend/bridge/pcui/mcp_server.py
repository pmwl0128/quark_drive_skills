"""`pcui mcp`: a stdio MCP server over the local bridge's own API (frontend/design.md §5).

The same session, artifact and operator calls as the pcui CLI, offered to an assistant as typed
tools. It is a thin adapter, not a second session protocol: it keeps no state, every call goes to
the running daemon, an artifact append carries the caller's revision, and a conflict comes back
exactly as the daemon reported it, never merged. Messages are newline-delimited JSON-RPC 2.0 on
stdin/stdout (UTF-8, whatever the console code page); anything else goes to stderr."""
from __future__ import annotations

import json
import math
import sys
import threading
from typing import Any, BinaryIO, Callable

from . import __version__
from .daemon_client import PANEL_KINDS, BridgeError, DaemonClient
from .operator_output import execution_failed, save_result

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Tools for Quark Drive Console sessions on this machine. Use them with the "
    "pokemon-champions-team skill's UEP workflow: read a session with session_show, run each gate "
    "operator with run_operator and session set (its artifacts are submitted for you), wait for the "
    "reader's decision or answers with session_wait, and give the reader open_link links.")


class ToolError(Exception):
    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.detail = detail


def _session_id(args: dict) -> str:
    value = args.get("session")
    if not isinstance(value, str) or not value:
        raise ToolError("`session` (a session id) is required")
    return value


def _title(view: dict) -> str:
    intent = view.get("meta", {}).get("intent")
    return view.get("title") or (intent if isinstance(intent, str) and intent else view["id"])


class Tools:
    """Each tool: a JSON Schema for its arguments and a handler returning JSON-able output."""

    def __init__(self, client: DaemonClient):
        self.client = client
        self.calls = threading.local()
        self.table: dict[str, tuple[str, dict, Callable[[dict], Any]]] = {
            "session_list": (
                "List Console sessions, newest first, with the state each is in (awaiting and "
                "questions wait on the reader).",
                {"type": "object", "properties": {
                    "archived": {"type": "string", "enum": ["exclude", "include", "only"]}}},
                self.session_list),
            "session_show": (
                "Read one session: meta (intent, format, requestKind, attached team), the latest "
                "artifact hash per kind, revision, ledger and live activity.",
                {"type": "object", "required": ["session"],
                 "properties": {"session": {"type": "string"}}},
                self.session_show),
            "session_create": (
                "Create a session for work that started in conversation, with the same intake meta "
                "the Console's form writes. Give the reader open_link for it.",
                {"type": "object", "required": ["intent"], "properties": {
                    "intent": {"type": "string"},
                    "format": {"type": "string", "enum": ["single", "double"]},
                    "kind": {"type": "string", "enum": ["build", "rework", "diagnose", "tune"]},
                    "team": {"type": "object", "description": "team-json to attach"},
                    "team_name": {"type": "string"}}},
                self.session_create),
            "artifact_get": (
                "The latest artifact of one kind in a session, verbatim.",
                {"type": "object", "required": ["session", "kind"],
                 "properties": {"session": {"type": "string"}, "kind": {"type": "string"}}},
                self.artifact_get),
            "artifact_put": (
                "Append an artifact no operator produced. `revision` must be the session revision "
                "you read; a conflict is returned as is (re-read, then decide).",
                {"type": "object", "required": ["session", "kind", "payload", "revision"], "properties": {
                    "session": {"type": "string"}, "kind": {"type": "string"},
                    "payload": {"description": "JSON text, or a JSON value to serialize"},
                    "revision": {"type": "integer", "minimum": 0}}},
                self.artifact_put),
            "run_operator": (
                "Run one whitelisted team.py operator in the daemon. File fields (file, context, "
                "slate, frame_output, slate_output, audit_receipt) take inline JSON. With `session`, "
                "the gate operators' artifacts are submitted to it. With `output_path`, the full "
                "result is written there and only a summary returns (use it for frame, "
                "slate-evaluate and diagnose).",
                {"type": "object", "required": ["op"], "properties": {
                    "op": {"type": "string"},
                    "args": {"type": "object", "description": "operator fields, as in a team.py session spec"},
                    "session": {"type": "string"},
                    "output_path": {"type": "string"}}},
                self.run_operator),
            "session_wait": (
                "Wait for the reader's answer in the panel (decision and/or intake-answer). Returns "
                "the artifact, or status timeout after a short poll: call again while waiting for the reader.",
                {"type": "object", "required": ["session"], "properties": {
                    "session": {"type": "string"},
                    "kinds": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": list(PANEL_KINDS)}},
                    "timeout": {"type": "number", "minimum": 1, "maximum": 50, "default": 25}}},
                self.session_wait),
            "open_link": (
                "A fresh one-time sign-in link to the Console, optionally straight to a session.",
                {"type": "object", "properties": {"session": {"type": "string"}}},
                self.open_link),
        }

    def session_list(self, args: dict) -> Any:
        archived = args.get("archived") or "exclude"
        views = self.client.request("GET", f"/api/sessions?archived={archived}")
        return {"sessions": [{key: value for key, value in {
            "id": view["id"], "title": _title(view), "state": view.get("state"),
            "lastKind": view.get("lastKind"), "updatedAt": view.get("updatedAt"),
            "format": view.get("meta", {}).get("format"), "requestKind": view.get("meta", {}).get("requestKind"),
            "activity": view.get("activity"), "waiting": view.get("waiting"), "archived": view.get("archived"),
        }.items() if value is not None} for view in views]}

    def session_show(self, args: dict) -> Any:
        return self.client.request("GET", DaemonClient.session_path(_session_id(args)))

    def session_create(self, args: dict) -> Any:
        intent = args.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            raise ToolError("`intent` is required")
        meta: dict = {"intent": intent.strip()}
        if args.get("kind"):
            meta["requestKind"] = args["kind"]
        team = args.get("team")
        fmt = args.get("format")
        if team is not None:
            if not isinstance(team, dict) or not isinstance(team.get("pokemon"), list) or not team["pokemon"]:
                raise ToolError("`team` must be a team-json object with a pokemon list")
            team_format = team.get("format") if team.get("format") in ("single", "double") else None
            if fmt and team_format and fmt != team_format:
                raise ToolError(f"the team is {team_format}, which contradicts format {fmt}")
            fmt = fmt or team_format
            meta["team"] = team
            if args.get("team_name"):
                meta["teamName"] = args["team_name"]
        if fmt:
            meta["format"] = fmt
        environment = self.client.request("GET", "/api/capabilities").get("environment")
        if isinstance(environment, dict):
            meta["environment"] = environment
        return self.client.request("POST", "/api/sessions", {"meta": meta})

    def artifact_get(self, args: dict) -> Any:
        session = self.session_show(args)
        kind = args.get("kind")
        head = session["heads"].get(kind) if isinstance(kind, str) else None
        if not head:
            raise ToolError(f"the session has no {kind!r} artifact")
        return self.client.text(f"/api/artifacts/{head}")

    def artifact_put(self, args: dict) -> Any:
        payload = args.get("payload")
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False, indent=2)
        revision = args.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ToolError("`revision` must be the session revision you read")
        return self.client.request("POST", DaemonClient.session_path(_session_id(args), "/artifacts"),
                                   {"kind": args.get("kind"), "payload": payload, "revision": revision})

    def run_operator(self, args: dict) -> Any:
        op = args.get("op")
        extra = args.get("args") or {}
        if not isinstance(op, str) or not isinstance(extra, dict):
            raise ToolError("`op` must be an operator name and `args` an object")
        target = args.get("output_path")
        if target is not None and (not isinstance(target, str) or not target or "\0" in target):
            raise ToolError("`output_path` must be a non-empty file path")
        body: dict = {"ops": [{**extra, "op": op}]}
        if args.get("session"):
            body["session"] = _session_id(args)
        out = self.client.request("POST", "/api/team/session", body, timeout=630)
        row = out[0] if isinstance(out, list) and len(out) == 1 else out
        if target:
            try:
                summary = save_result(row, target)
            except OSError as exc:
                raise ToolError(f"operator finished but saving output failed: {exc}",
                                {"operatorCompleted": True, "row": row}) from exc
        else:
            summary = row
        if execution_failed(row):
            raise ToolError("operator execution failed", summary)
        return summary

    def session_wait(self, args: dict) -> Any:
        kinds = args.get("kinds", list(PANEL_KINDS))
        timeout = args.get("timeout", 25)
        if (not isinstance(timeout, (float, int)) or isinstance(timeout, bool) or not math.isfinite(timeout)
                or not 1 <= timeout <= 50):
            raise ToolError("`timeout` must be a number between 1 and 50 seconds")
        if not isinstance(kinds, list) or not kinds or any(kind not in PANEL_KINDS for kind in kinds):
            raise ToolError("`kinds` must contain decision and/or intake-answer")
        answer = self.client.wait_for_reader(_session_id(args), kinds, timeout,
                                             cancel=getattr(self.calls, "cancel", None))
        return {"status": "timeout"} if answer is None else {"status": "ready", **answer}

    def open_link(self, args: dict) -> Any:
        token = self.client.request("POST", "/api/bootstrap/mint", {})["token"]
        target = DaemonClient.session_path(args["session"]).removeprefix("/api/") if args.get("session") else ""
        return {"url": f"http://127.0.0.1:{self.client.port()}/{target}#bootstrap={token}"}


class McpServer:
    def __init__(self, client: DaemonClient | None = None):
        self.tools = Tools(client or DaemonClient())

    def _call(self, params: dict, cancel: threading.Event | None = None) -> dict:
        name = params.get("name")
        if name not in self.tools.table:
            raise LookupError(f"unknown tool: {name}")
        try:
            args = params.get("arguments", {})
            if not isinstance(args, dict):
                raise ToolError("`arguments` must be an object")
            self.tools.calls.cancel = cancel
            out = self.tools.table[name][2](args)
        except BridgeError as exc:
            detail = {"error": str(exc), "status": exc.status, "detail": exc.detail}
            return {"content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False, indent=2)}],
                    "isError": True}
        except ToolError as exc:
            text = json.dumps({"error": str(exc), "detail": exc.detail}, ensure_ascii=False, indent=2)
            return {"content": [{"type": "text", "text": text}], "isError": True}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2)
        result: dict = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(out, dict):
            result["structuredContent"] = out
        return result

    def handle(self, message: Any, cancel: threading.Event | None = None) -> dict | None:
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if "id" not in message:                 # a notification: never answered
            return None
        method, params = message["method"], message.get("params") or {}
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": "params must be an object"}}
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                result: Any = {
                    "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "quark-drive", "version": __version__},
                    "instructions": INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": description, "inputSchema": schema}
                                    for name, (description, schema, _) in self.tools.table.items()]}
            elif method == "tools/call":
                result = self._call(params, cancel)
            else:
                return {"jsonrpc": "2.0", "id": message["id"],
                        "error": {"code": -32601, "message": f"method not found: {method}"}}
        except (LookupError, TypeError, ValueError) as exc:
            return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None,
          client: DaemonClient | None = None) -> int:
    server = McpServer(client)
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    output_lock = threading.Lock()
    active_lock = threading.Lock()
    active: dict[Any, threading.Event] = {}
    threads: list[threading.Thread] = []

    def write(reply):
        if reply is not None:
            with output_lock:
                sink.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
                sink.flush()

    def respond(message, cancel=None, emit=write):
        try:
            reply = server.handle(message, cancel)
        except Exception:
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32603, "message": "internal error"}}
        emit(reply if cancel is None or not cancel.is_set() else None)

    def worker(message, cancel, emit):
        try:
            respond(message, cancel, emit)
        finally:
            with active_lock:
                if active.get(message["id"]) is cancel:
                    active.pop(message["id"], None)

    def dispatch(message, emit=write):
        if isinstance(message, list) and message:
            # Older negotiated protocol versions allow JSON-RPC batches. Their slow calls must not
            # monopolize the input reader either; collect one batch reply as each member settles.
            remaining = len(message)
            replies = []
            lock = threading.Lock()
            def collect(reply):
                nonlocal remaining
                with lock:
                    if reply is not None:
                        replies.append(reply)
                    remaining -= 1
                    if remaining == 0:
                        emit(replies or None)
            for member in message:
                dispatch(member, collect)
            return
        if isinstance(message, dict) and message.get("method") == "notifications/cancelled" and "id" not in message:
            params = message.get("params")
            request_id = params.get("requestId") if isinstance(params, dict) else None
            with active_lock:
                cancel = active.get(request_id) if isinstance(request_id, (str, int)) and not isinstance(request_id, bool) else None
                if cancel is not None:
                    cancel.set()
            emit(None)
        elif isinstance(message, dict) and message.get("method") == "tools/call" and "id" in message:
            request_id = message["id"]
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request id"}})
                return
            with active_lock:
                busy = request_id in active or len(active) >= 8
                if not busy:
                    cancel = threading.Event()
                    active[request_id] = cancel
            if busy:
                emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "too many active calls or duplicate id"}})
            else:
                thread = threading.Thread(target=worker, args=(message, cancel, emit), daemon=True)
                threads[:] = [t for t in threads if t.is_alive()]
                threads.append(thread)
                thread.start()
        else:
            respond(message, emit=emit)

    for raw in source:
        try:
            line = raw.decode("utf-8").strip()
            if line:
                dispatch(json.loads(line))
        except (ValueError, UnicodeError):
            write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        except Exception:
            write({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "internal error"}})
    # EOF ends the connection, not an invitation to wait for a ten-minute operator. Short calls may
    # finish their reply; daemon workers cannot keep the stdio process alive after the host exits.
    with active_lock:
        for cancel in active.values():
            cancel.set()
    for thread in threads:
        thread.join(timeout=0.1)
    return 0
