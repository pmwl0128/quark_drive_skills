"""Local-bridge security (frontend/design.md §4.3).

- Browser channel: a ONE-TIME bootstrap token printed in the launch URL's #fragment
  (fragments never hit history/logs/Referer); POST /api/bootstrap exchanges it for a
  short-lived HttpOnly SameSite=Strict cookie. The token dies on first use. `pcui open` asks the
  daemon (CLI secret only) for further one-time tokens, so a second window or a reopened tab can
  sign in without restarting the bridge; each expires unused after a few minutes.
- CLI channel: a per-daemon secret written to daemon.json (0600 best-effort), sent as
  the X-PCUI-Secret header — same-machine file-system trust.
- Host/Origin gate: only localhost hosts; when an Origin header is present it must be a
  localhost origin (browser cross-site calls die here even with a cookie).
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from .paths import data_dir

COOKIE_NAME = "pcui_session"
MINTED_TOKEN_TTL = 600.0
_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _host_only(host: str | None) -> str:
    """Hostname from a Host/authority string, port stripped, IPv6 brackets removed.
    `[::1]:8763` -> `::1`, `127.0.0.1:8763` -> `127.0.0.1`, `localhost` -> `localhost`."""
    h = (host or "").strip()
    if h.startswith("["):                      # [ipv6] or [ipv6]:port
        return h[1:].split("]", 1)[0].lower()
    return h.split(":", 1)[0].lower()          # hostname/ipv4, drop :port


def _same(left: str, right: str) -> bool:
    # compare_digest refuses non-ASCII str; compare bytes so hostile input is a plain mismatch.
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class Security:
    def __init__(self) -> None:
        self.bootstrap_token: str | None = secrets.token_urlsafe(24)
        self.cli_secret = secrets.token_urlsafe(24)
        self._cookie_value = secrets.token_urlsafe(24)
        self._minted: dict[str, float] = {}       # token -> monotonic expiry

    # -- browser bootstrap ------------------------------------------------------

    def mint_bootstrap(self) -> str:
        """Another one-time token for `pcui open`, with the same exchange as the launch token."""
        now = time.monotonic()
        self._minted = {token: expiry for token, expiry in self._minted.items() if expiry > now}
        token = secrets.token_urlsafe(24)
        self._minted[token] = now + MINTED_TOKEN_TTL
        return token

    def consume_bootstrap(self, token: str) -> str | None:
        """Exchange a one-time token for the session cookie value (None = reject)."""
        if self.bootstrap_token is not None and _same(token, self.bootstrap_token):
            self.bootstrap_token = None
            return self._cookie_value
        for candidate, expiry in list(self._minted.items()):
            if _same(token, candidate):
                del self._minted[candidate]
                return self._cookie_value if expiry > time.monotonic() else None
        return None

    def is_cli(self, secret_header: str | None) -> bool:
        return secret_header is not None and _same(secret_header, self.cli_secret)

    def is_authenticated(self, cookie: str | None, secret_header: str | None) -> bool:
        if cookie is not None and _same(cookie, self._cookie_value):
            return True
        return self.is_cli(secret_header)

    # -- host / origin gate -------------------------------------------------------

    @staticmethod
    def host_allowed(host: str | None) -> bool:
        return _host_only(host) in _ALLOWED_HOSTNAMES

    @staticmethod
    def origin_allowed(origin: str | None) -> bool:
        if not origin:
            return True                       # non-browser clients send no Origin
        try:
            hostpart = origin.split("://", 1)[1]
        except IndexError:
            return False
        return Security.host_allowed(hostpart)


def claim_daemon_file(port: int, secret: str) -> bool:
    """Atomically claim daemon.json for THIS process (the marker of a RUNNING daemon: port +
    pid + CLI secret). Returns False if a LIVE daemon already owns it.

    The claim is exclusive and content-complete: write a private temp, chmod it, then hard-
    link it into place. A concurrent second `serve` therefore can neither overwrite a live
    marker (which previously let it delete the winner's marker on its own bind failure —
    split-brain, external audit 2026-07-13) nor observe a half-written one. A stale/corrupt
    marker from a dead pid is cleared and the claim retried."""
    path = data_dir() / "daemon.json"
    payload = json.dumps({"port": port, "pid": os.getpid(), "secret": secret})
    for _ in range(2):
        fd, tmp = tempfile.mkstemp(dir=str(data_dir()), prefix=".daemon-", suffix=".tmp")
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            try:
                os.chmod(tmp, 0o600)          # perms set BEFORE the secret is visible
            except OSError:                   # best-effort on Windows
                pass
            try:
                os.link(tmp, path)            # atomic, exclusive, content already in place
                return True
            except FileExistsError:
                info = read_daemon_file()
                if info is not None and pid_alive(int(info.get("pid", -1))):
                    return False              # a live daemon holds it — never disturb
                path.unlink(missing_ok=True)  # stale/corrupt leftover — clear and retry
            except OSError:                   # filesystem without hardlinks (FAT/exotic):
                os.replace(tmp, path)         # atomic overwrite — content-complete, no window
                return True
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return False


def read_daemon_file() -> dict | None:
    path = data_dir() / "daemon.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def remove_daemon_file_if_owned() -> bool:
    """Delete daemon.json ONLY when this process wrote it. A failed second `serve` (port in
    use) must not erase the live daemon's marker — that would flip the CLI into
    direct-SQLite mode against a running daemon (split-brain; external audit 2026-07-13)."""
    info = read_daemon_file()
    if info is None or int(info.get("pid", -1)) != os.getpid():
        return False
    (data_dir() / "daemon.json").unlink(missing_ok=True)
    return True


def pid_alive(pid: int) -> bool:
    """Is `pid` a live process? A permission failure means the process EXISTS but is not
    ours to probe (root/other-user/service daemon) — it MUST count as alive, or _daemon()
    misreads a running daemon as dead and degrades to direct SQLite writes (split-brain).

    Uses only stdlib: an optional psutil fast-path was removed once these native branches
    handle the permission-denied case correctly, so there is no undeclared dependency whose
    presence/absence silently changes the liveness verdict."""
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if handle:
            # An OPEN handle is not liveness: a terminated process whose handle someone
            # still holds (e.g. the parent shell's Popen) keeps its process object around,
            # and OpenProcess happily opens it — a zombie then blocks `pcui serve` restarts
            # ("already running") forever. Ask for the exit code: STILL_ACTIVE(259) = alive.
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(ok) and exit_code.value == 259
        # NULL handle: distinguish "no such process" from "exists but access denied".
        return kernel32.GetLastError() == 5  # ERROR_ACCESS_DENIED -> process exists
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:                 # ESRCH: no such process
        return False
    except PermissionError:                    # EPERM: exists, not ours
        return True
