"""A client of the running local bridge, shared by the CLI's long-running commands and the MCP
adapter. It reads daemon.json on every request, so a restarted bridge (new port, new secret) is
found again without restarting the caller."""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .security import pid_alive, read_daemon_file

WAIT_POLL = 25
PANEL_KINDS = ("decision", "intake-answer")


class BridgeError(Exception):
    """The bridge refused a request (`status`, with its JSON `detail`), or is not running (0)."""

    def __init__(self, status: int, message: str, detail: Any = None):
        super().__init__(message)
        self.status, self.detail = status, detail


class DaemonClient:
    def __init__(self, info: Callable[[], dict | None] | None = None):
        self._info = info or self._read_info

    @staticmethod
    def _read_info() -> dict | None:
        info = read_daemon_file()
        return info if info and pid_alive(int(info.get("pid", -1))) else None

    def _open(self, method: str, path: str, body: Any = None, timeout: float = 30):
        info = self._info()
        if info is None:
            raise BridgeError(0, "the local bridge is not running; start it with `pcui serve`")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}{path}", method=method, data=data,
            headers={"X-PCUI-Secret": info["secret"], "Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise BridgeError(exc.code, f"bridge answered {exc.code} for {path}", detail) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BridgeError(0, f"the local bridge did not answer: {exc}") from exc

    def port(self) -> int | None:
        info = self._info()
        return int(info["port"]) if info else None

    def request(self, method: str, path: str, body: Any = None, timeout: float = 30) -> Any:
        with self._open(method, path, body, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def text(self, path: str, timeout: float = 30) -> str:
        with self._open("GET", path, None, timeout) as resp:
            return resp.read().decode("utf-8")

    @staticmethod
    def session_path(session_id: str, suffix: str = "") -> str:
        return f"/api/sessions/{urllib.parse.quote(session_id, safe='')}{suffix}"

    def wait_for_reader(self, session_id: str, kinds: list[str], timeout: float,
                        cancel: threading.Event | None = None) -> dict | None:
        """Block until the reader answers in the panel; {kind, payload} or None at `timeout`.

        The reader's move counts once it follows the assistant's own last artifact, so an answer
        given before the wait began returns at once. A dropped poll or a restarting bridge is
        retried until the deadline; a session that no longer exists raises."""
        if not kinds or any(kind not in PANEL_KINDS for kind in kinds):
            raise BridgeError(400, "kinds must contain decision and/or intake-answer")
        if not math.isfinite(timeout) or timeout <= 0:
            raise BridgeError(400, "timeout must be a positive finite number")
        if cancel is not None and cancel.is_set():
            return None
        after = 0
        for entry in self.request("GET", self.session_path(session_id))["ledger"]:
            if entry["kind"] not in kinds:
                after = entry["sequence"] + 1
        deadline = time.monotonic() + timeout
        query = urllib.parse.urlencode({"kinds": ",".join(kinds), "after": after})
        while (left := deadline - time.monotonic()) > 0:
            if cancel is not None and cancel.is_set():
                return None
            try:
                poll = 1 if cancel is not None else WAIT_POLL
                out = self.request(
                    "GET", self.session_path(session_id, f"/wait?{query}&timeout={max(1, min(poll, int(left)))}"),
                    timeout=poll + 5)
            except BridgeError as exc:
                if exc.status not in (0, 429, 502, 503, 504):
                    raise
                delay = min(2.0, max(0.0, deadline - time.monotonic()))
                if cancel is not None:
                    cancel.wait(delay)
                else:
                    time.sleep(delay)
                continue
            if out.get("status") == "ready":
                return {"kind": out.get("kind"), "payload": self.text(f"/api/artifacts/{out['artifactHash']}")}
        return None
