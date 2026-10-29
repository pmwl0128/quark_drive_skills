"""pcui command line: serve / doctor / open / session / artifact / run / install-agent.

Connects to a RUNNING daemon over HTTP (X-PCUI-Secret from daemon.json). Direct-SQLite
degradation is allowed ONLY when the daemon is provably not running (daemon.json absent,
stale, or its pid dead) — a running-but-unreachable daemon is an ERROR, never a silent
direct write (frontend/design.md §4.2 🚩 split-brain rule)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from . import cli_i18n as i18n
from .agent_setup import (COMPANION_NAME, TARGETS, agent_skill_dir, companion_status,
                          pcui_argv, pcui_command, render_companion, shell_argv)
from .config import (CONFIG_ENV_NAME, EnvFileError, env_file_values,
                     load_runtime_config)
from .paths import CLIS, META_DATA, PROJECT_ROOT, SKILLS_ROOT, data_dir
from .security import pid_alive, read_daemon_file
from .store import Conflict, Store


class DaemonUnreachable(SystemExit):
    pass


def _load_env_file(path: Path) -> None:
    """Load a systemd-EnvironmentFile-style ``KEY=value`` file without executing it.

    The local online rehearsal uses the same repository-external secret shape as production.
    An already-set process variable wins, so systemd or a one-off shell override remains the
    authority. Duplicate keys inside the file follow the usual last-assignment-wins rule.
    """
    try:
        values = env_file_values(path)
    except EnvFileError as exc:
        raise SystemExit(i18n.t("env_file_invalid", path=exc.path, line=exc.line)) from exc
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _daemon() -> dict | None:
    """Running daemon's {port, secret}, None if provably not running, SystemExit if
    running-but-unreachable."""
    info = read_daemon_file()
    if info is None:
        return None
    if not pid_alive(int(info.get("pid", -1))):
        return None
    try:
        _http(info, "GET", "/api/health")      # O(1) probe, not the full session list
        return info
    except urllib.error.URLError:
        raise DaemonUnreachable(
            i18n.t("daemon_unreachable", pid=info["pid"], port=info["port"]))


# Mirror the daemon's server.MAX_BODY. An oversized POST is rejected by the daemon's body-size gate
# BEFORE the request body is drained, so on Windows the socket closes with unread inbound bytes and
# the client's in-progress send() surfaces a bare ConnectionResetError (WinError 10054) instead of
# the server's 413 — the response is never readable. Pre-check the size here so the caller gets an
# actionable message (post a compact/summary artifact) instead of an opaque connection-reset traceback.
MAX_REQUEST_BODY = 2 * 1024 * 1024


def _http(info: dict, method: str, path: str, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None and len(data) > MAX_REQUEST_BODY:
        raise SystemExit(
            f"pcui: {path} request body is {len(data):,} bytes, over the daemon's "
            f"{MAX_REQUEST_BODY // (1024 * 1024)} MiB body limit. The daemon would reject it and reset "
            f"the connection — post a compact/summary payload instead (e.g. a trimmed slate artifact).")
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}{path}", method=method,
        data=data,
        headers={"X-PCUI-Secret": info["secret"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _remembered_workspace(value: str) -> Path:
    from .workspace import WorkspaceError, configured_workspace
    try:
        return configured_workspace(value)
    except WorkspaceError as exc:
        raise SystemExit(i18n.t("workspace_missing", path=value)) from exc


def _store() -> Store:
    from .workspace import default_workspace
    config = load_runtime_config()
    if config.workspace_dir:
        workspace = _remembered_workspace(config.workspace_dir)
    else:
        workspace = default_workspace()
        workspace.mkdir(parents=True, exist_ok=True)
    return Store(workspace / "sessions.db")


CURRENT_SESSION_FILE = "current-session"


def _session_scope() -> str:
    daemon = _daemon()
    if daemon:
        return _http(daemon, "GET", "/api/workspace")["workspaceId"]
    from .workspace import default_workspace
    config = load_runtime_config()
    folder = Path(config.workspace_dir) if config.workspace_dir else default_workspace()
    return os.path.normcase(str(folder.expanduser().resolve()))


def _current_session() -> str | None:
    """Human CLI convenience only; assistants always pass an explicit session id."""
    value = os.environ.get("PCUI_SESSION", "").strip()
    if value:
        return value
    try:
        current = json.loads((data_dir() / CURRENT_SESSION_FILE).read_text(encoding="utf-8"))
        return current["session"] if isinstance(current, dict) and current.get("workspace") == _session_scope() else None
    except (OSError, ValueError, KeyError):
        return None


def _session_id(explicit: str | None) -> str:
    session_id = explicit or _current_session()
    if not session_id:
        raise SystemExit(i18n.t("need_session"))
    return session_id


def _projection_deployment_id(value: str | None) -> str | None:
    """Bind a packaged local bridge to the immutable projection it serves."""
    if not value:
        return None
    manifest = Path(value) / "manifest.json"
    try:
        deployment_id = json.loads(manifest.read_text(encoding="utf-8")).get("deploymentId")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid projection manifest: {manifest}: {exc}") from exc
    if not isinstance(deployment_id, str) or not deployment_id:
        raise SystemExit(f"projection manifest has no deploymentId: {manifest}")
    return deployment_id


def _local_unmetered(enabled: bool, host: str, public_origin: str | None) -> bool:
    """Resolve the local-only quota bypass without consulting any request data."""
    if enabled and (host not in {"127.0.0.1", "::1", "localhost"} or public_origin):
        raise ValueError(i18n.t("unlimited_refused"))
    return enabled


def cmd_serve(ns) -> int:
    import uvicorn

    from .security import Security, claim_daemon_file, remove_daemon_file_if_owned
    from .server import create_app
    from .worker import WorkerPool

    if ns.host != "127.0.0.1":
        print(i18n.t("expose_local", host=ns.host))
        if input(i18n.t("confirm")).strip() != "yes":
            return 1
    existing = read_daemon_file()
    if existing and pid_alive(int(existing.get("pid", -1))):
        raise SystemExit(i18n.t("daemon_running", pid=existing["pid"], port=existing["port"]))
    from .workspace import Workspace, default_workspace, save_workspace_default
    remembered = load_runtime_config(Path(ns.workspace_config)).workspace_dir
    if ns.workspace:
        chosen = Path(ns.workspace).expanduser().resolve()
    elif remembered:
        chosen = _remembered_workspace(remembered)
    else:
        chosen = default_workspace().resolve()
    security = Security()
    pool = WorkerPool()
    workspace = Workspace(chosen, Path(ns.workspace_config))
    legacy = data_dir() / "sessions.db"
    first_workspace = remembered is None
    if first_workspace and not (workspace.path / "sessions.db").exists() and legacy.is_file() and legacy.resolve() != workspace.path / "sessions.db":
        previous = Store(legacy)
        try:
            previous.backup(workspace.path / "sessions.db")
        finally:
            previous.close()
    store = Store(workspace.path / "sessions.db")
    app = create_app(store, pool, security,
                     workspace=workspace,
                     deployment_id=_projection_deployment_id(ns.projection),
                     dist_dir=Path(ns.dist) if ns.dist else None,
                     projection_dir=Path(ns.projection) if ns.projection else None,
                     bind_host=ns.host)
    # Atomic exclusive claim: closes the TOCTOU between the check above and marker creation —
    # a concurrent `serve` that lost the race here cannot clobber the winner's marker.
    if not claim_daemon_file(ns.port, security.cli_secret):
        pool.shutdown()
        app.state.close_workspace()
        live = read_daemon_file() or {}
        raise SystemExit(i18n.t("daemon_running", pid=live.get("pid", "?"), port=live.get("port", "?")))
    # flush: under a pipe (wrapper scripts, log capture) stdout is block-buffered and the
    # banner — the ONLY place the bootstrap token appears — would otherwise arrive late.
    print(f"pcui bridge: http://127.0.0.1:{ns.port}/#bootstrap={security.bootstrap_token}",
          flush=True)
    try:
        save_workspace_default(Path(ns.workspace_config), workspace.path)
        uvicorn.run(app, host=ns.host, port=ns.port, log_level="warning")
    finally:
        pool.shutdown()
        app.state.close_workspace()
        remove_daemon_file_if_owned()      # never erase a marker this process did not write
    return 0


def cmd_online_serve(ns) -> int:
    """The PUBLIC online API (frontend/design.md §7) — an independent process, not the local
    bridge: no daemon.json claim, no bootstrap token; anonymity + limits are the security
    model. Binds localhost by default (production puts Nginx in front, §8)."""
    import uvicorn

    from .online.jobs import JobStore
    from .online.limits import OnlineLimits, QaLimitConfig
    from .online.provider import (AnthropicMessagesConfig, AnthropicMessagesProvider,
                                  EchoProvider, FallbackProvider, OpenAIChatConfig,
                                  OpenAICompatibleProvider)
    from .online.server import create_online_app
    from .online.access_log import AccessLog
    from .online.tester_keys import TesterKeyStore
    from .worker import WorkerPool

    _load_env_file(Path(ns.env_file))

    public_origin = os.environ.get("PCUI_PUBLIC_ORIGIN") or None
    try:
        unmetered = _local_unmetered(
            bool(ns.local_online_unlimited), ns.host, public_origin)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if ns.host != "127.0.0.1":
        print(i18n.t("expose_online", host=ns.host))
        if input(i18n.t("confirm")).strip() != "yes":
            return 1
    qa_provider = None  # questions use the ordinary (non-thinking) chain
    if ns.provider == "echo":
        provider = EchoProvider()
        thinking_provider = provider
    elif ns.provider in {"opencode", "deepseek"}:
        # opencode: OpenCode Go's DeepSeek Flash first, DeepSeek's official API as the second route.
        try:
            official = AnthropicMessagesConfig.from_env(os.environ, provider="deepseek")
            primary = (OpenAIChatConfig.from_env(os.environ, provider="opencode")
                       if ns.provider == "opencode" else None)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        bases = ([OpenAICompatibleProvider(primary)] if primary and primary.api_key else []) + \
            ([AnthropicMessagesProvider(official)] if official.api_key else [])
        provider = thinking_provider = None
        for base in reversed(bases):
            plain, deep = base.with_thinking(False), base.with_thinking(True)
            provider = FallbackProvider(plain, provider) if provider else plain
            thinking_provider = FallbackProvider(deep, thinking_provider) if thinking_provider else deep
        if provider is None:
            print(i18n.t("llm_key_missing"))
    elif ns.provider == "openai-compatible":
        try:
            llm_config = OpenAIChatConfig.from_env(os.environ)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        base_provider = OpenAICompatibleProvider(llm_config) if llm_config.api_key else None
        provider = base_provider.with_thinking(False) if base_provider else None
        thinking_provider = base_provider.with_thinking(True) if base_provider else None
        if provider is None:
            print(i18n.t("llm_key_missing"))
    else:
        provider = None
        thinking_provider = None
    secret = os.environ.get("PCUI_ONLINE_SECRET", "")
    if not secret:
        import secrets as _secrets
        secret = _secrets.token_hex(16)
        print(i18n.t("secret_missing"))
    try:
        code_daily_limit = int(os.environ.get("PCUI_TEAMCODE_VISITOR_DAILY_LIMIT", "10"))
        if code_daily_limit < 1:
            raise ValueError
    except ValueError:
        raise SystemExit("PCUI_TEAMCODE_VISITOR_DAILY_LIMIT must be a positive integer") from None
    limits = OnlineLimits(data_dir() / "online.db", secret,
                          QaLimitConfig(daily_limit=ns.qa_daily_limit,
                                        daily_token_budget=ns.qa_token_budget,
                                        team_code_daily_limit=code_daily_limit))
    dev_key = os.environ.get("PCUI_DEV_KEY") or None
    smoke_key = os.environ.get("PCUI_SMOKE_KEY") or None
    tester_keys = TesterKeyStore(data_dir() / "tester-keys.db")
    access_log = AccessLog(data_dir() / "access-log.db")
    pool = WorkerPool()
    app = create_online_app(
        pool, provider, limits, thinking_provider=thinking_provider, qa_provider=qa_provider,
        dist_dir=Path(ns.dist) if ns.dist else None,
        projection_dir=Path(ns.projection) if ns.projection else None,
        deployment_id=_projection_deployment_id(ns.projection),
        public_origin=public_origin,
        idle_reap_seconds=ns.worker_idle if ns.worker_idle > 0 else None,
        deterministic_workers=ns.deterministic_workers,
        dev_key=dev_key,
        smoke_key=smoke_key,
        tester_keys=tester_keys,
        access_log=access_log,
        unmetered=unmetered,
        # web_jobs (§7.3): transient builder-job rows share online.db — same retention
        # discipline as the limits tables, swept on traffic.
        jobs=JobStore(data_dir() / "online.db"))
    provider_summary = getattr(provider, "safe_summary", None)
    provider_text = ((f"{provider_summary()}, "
                      "diagnose/builder=optional thinking")
                     if provider_summary
                     else ns.provider if provider else "none")
    print(f"pcui online API: http://127.0.0.1:{ns.port}/ "
          f"(provider: {provider_text}"
          f"{', local unmetered' if unmetered else ''}"
          f"{', browser access enabled' if dev_key else ''}"
          f"{', smoke access enabled' if smoke_key else ''})", flush=True)
    qa_summary = getattr(qa_provider or provider, "safe_summary", None)
    if qa_summary:
        print(f"pcui Q&A: {qa_summary()}", flush=True)
    try:
        uvicorn.run(app, host=ns.host, port=ns.port, log_level="warning")
    finally:
        pool.shutdown()
    return 0


def cmd_doctor(ns) -> int:
    checks: list[tuple[str, bool, str]] = []
    for cli_id, path in CLIS.items():
        checks.append((i18n.t("check_skill", name=cli_id), path.exists(), str(path)))
    mirror = SKILLS_ROOT.parent.parent / ".claude" / "skills"
    checks.append((i18n.t("check_mirror"), mirror.is_dir(), str(mirror)))
    try:
        import quickjs  # type: ignore  # noqa: F401
        checks.append((i18n.t("check_quickjs"), True, i18n.t("calc_runtime")))
    except ImportError:
        node = shutil.which("node")
        checks.append((i18n.t("check_node"), node is not None, node or i18n.t("calc_missing")))
    current = META_DATA / "current.json"
    checks.append((i18n.t("check_meta"), current.exists(), str(current)))
    from .workspace import default_workspace, workspace_path
    config = load_runtime_config()
    folder = workspace_path(config.workspace_dir) if config.workspace_dir else default_workspace()
    checks.append((i18n.t("check_write"), folder.is_dir() or folder.parent.is_dir(), str(folder / "sessions.db")))
    info = read_daemon_file()
    running = bool(info and pid_alive(int(info.get("pid", -1))))
    checks.append((i18n.t("check_daemon"), True,
                   i18n.t("running", port=info["port"]) if running else i18n.t("not_running")))

    ok = True
    for name, passed, detail in checks:
        ok &= passed
        status = i18n.t("status_ok" if passed else "status_fail")
        print(f"  [{status}] {name}: {detail}")
    if ok:
        # end-to-end: one real dex query through a one-shot subprocess
        from .runner import run_cli
        probe = json.loads(run_cli("dex", ["resolve", "Garchomp", "--format", "json"])["stdout"])
        good = bool(probe and probe[0].get("ok"))
        status = i18n.t("status_ok" if good else "status_fail")
        print(f"  [{status}] {i18n.t('check_probe')}")
        ok &= good
    # The assistant link is optional for browsing, so it informs without failing the check.
    statuses = {target: companion_status(agent_skill_dir(target)) for target in TARGETS}
    for target, status in statuses.items():
        print(f"  [{i18n.t('companion_' + status)}] {i18n.t('check_companion', target=target)}")
    if "installed" not in statuses.values():
        print(i18n.t("companion_hint", cmd=pcui_command()))
    print(i18n.t("doctor_ok" if ok else "doctor_fail"))
    return 0 if ok else 1


def cmd_open(ns) -> int:
    """Print (and open) a fresh one-time sign-in link, optionally straight to one session."""
    daemon = _daemon()
    if daemon is None:
        raise SystemExit(i18n.t("open_need_daemon"))
    token = _http(daemon, "POST", "/api/bootstrap/mint", {})["token"]
    target = f"sessions/{urllib.parse.quote(ns.session, safe='')}" if ns.session else ""
    url = f"http://127.0.0.1:{daemon['port']}/{target}#bootstrap={token}"
    print(url, flush=True)
    if not ns.no_browser:
        webbrowser.open(url)
    return 0


def mcp_config() -> str:
    """How to register `pcui mcp` with the assistants pcui knows; registration stays the reader's
    own step (design §5: nothing is injected into an agent)."""
    argv = [*pcui_argv(), "mcp"]
    toml_args = ", ".join(json.dumps(part, ensure_ascii=False) for part in argv[1:])
    return (f"Claude Code:\n  claude mcp add quark-drive -- {shell_argv(argv)}\n\n"
            f"Codex (~/.codex/config.toml):\n  [mcp_servers.quark-drive]\n"
            f"  command = {json.dumps(argv[0], ensure_ascii=False)}\n  args = [{toml_args}]\n"
            f"  tool_timeout_sec = 660\n")


def cmd_mcp(ns) -> int:
    if ns.print_config:
        print(mcp_config(), end="")
        return 0
    from .mcp_server import serve
    return serve()


def _session_meta(ns, daemon: dict | None) -> dict:
    """The same intake meta the Console's new-task form writes (frontend/design.md §6)."""
    meta: dict = {}
    if ns.intent and ns.intent.strip():
        meta["intent"] = ns.intent.strip()
    if ns.kind:
        meta["requestKind"] = ns.kind
    fmt = ns.format
    if ns.team:
        try:
            team = json.loads(Path(ns.team).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise SystemExit(i18n.t("team_invalid", path=ns.team)) from exc
        if not isinstance(team, dict) or not isinstance(team.get("pokemon"), list) or not team["pokemon"]:
            raise SystemExit(i18n.t("team_invalid", path=ns.team))
        team_format = team.get("format") if team.get("format") in ("single", "double") else None
        if fmt and team_format and fmt != team_format:
            raise SystemExit(i18n.t("team_format_conflict", team=team_format, given=fmt))
        fmt = fmt or team_format
        meta["team"] = team
        if ns.team_name:
            meta["teamName"] = ns.team_name
    if fmt:
        meta["format"] = fmt
    if daemon:
        environment = _http(daemon, "GET", "/api/capabilities").get("environment")
        if isinstance(environment, dict):
            meta["environment"] = environment
    return meta


def _read_session(daemon: dict | None, session_id: str) -> dict:
    if daemon:
        try:
            return _http(daemon, "GET", f"/api/sessions/{urllib.parse.quote(session_id, safe='')}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SystemExit(i18n.t("session_not_found", id=session_id)) from exc
            raise
    from .store import NotFound
    s = _store()
    try:
        out = s.get_session(session_id)
    except NotFound as exc:
        raise SystemExit(i18n.t("session_not_found", id=session_id)) from exc
    out["ledger"] = s.session_ledger(session_id)
    return out


def cmd_session(ns) -> int:
    daemon = _daemon()
    explicit = ns.id or ns.session_id
    if ns.action == "create":
        meta = _session_meta(ns, daemon)
        out = (_http(daemon, "POST", "/api/sessions", {"meta": meta}) if daemon
               else _store().create_session(meta))
    elif ns.action == "list":
        out = (_http(daemon, "GET", "/api/sessions") if daemon
               else _store().list_sessions())
    elif ns.action == "use":
        if not explicit:
            current = _current_session()
            print(i18n.t("session_current", id=current) if current else i18n.t("session_none"))
            return 0
        _read_session(daemon, explicit)
        (data_dir() / CURRENT_SESSION_FILE).write_text(
            json.dumps({"session": explicit, "workspace": _session_scope()}) + "\n", encoding="utf-8")
        print(i18n.t("session_current", id=explicit))
        return 0
    elif ns.action == "wait":
        return _wait(daemon, _session_id(explicit), ns.wait_for, ns.timeout)
    else:                                     # show
        out = _read_session(daemon, _session_id(explicit))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _wait(daemon: dict | None, session_id: str, kinds: str, timeout: float) -> int:
    """Block until the reader answers in the panel, then print that artifact verbatim (rc 3 when
    `timeout` passes first)."""
    from .daemon_client import BridgeError, DaemonClient
    if daemon is None:
        raise SystemExit(i18n.t("wait_need_daemon"))
    wanted = [kind for kind in kinds.split(",") if kind]
    print(i18n.t("wait_started", id=session_id, kinds=", ".join(wanted)), file=sys.stderr, flush=True)
    try:
        answer = DaemonClient().wait_for_reader(session_id, wanted, timeout)
    except BridgeError as exc:
        raise SystemExit(i18n.t("session_not_found", id=session_id) if exc.status == 404 else str(exc)) from exc
    if answer is None:
        print(i18n.t("wait_timeout", id=session_id), file=sys.stderr)
        return 3
    print(i18n.t("wait_ready", kind=answer["kind"]), file=sys.stderr, flush=True)
    sys.stdout.write(answer["payload"])
    return 0


def cmd_artifact(ns) -> int:
    daemon = _daemon()
    ns.session = _session_id(ns.session)
    if ns.action == "put":
        if ns.revision is None or ns.revision < 0:
            raise SystemExit(i18n.t("artifact_revision_required"))
        payload = Path(ns.file).read_text(encoding="utf-8") if ns.file else sys.stdin.read()
        if daemon:
            try:
                out = _http(daemon, "POST", f"/api/sessions/{ns.session}/artifacts",
                            {"kind": ns.kind, "payload": payload, "revision": ns.revision})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                detail = json.loads(exc.read())["detail"]
                raise SystemExit(i18n.t("artifact_conflict", expected=detail["expected"],
                                        actual=detail["actual"])) from exc
        else:
            s = _store()
            try:
                out = s.append_artifact(ns.session, ns.kind, payload.encode("utf-8"), ns.revision)
            except Conflict as exc:
                raise SystemExit(i18n.t("artifact_conflict", expected=exc.expected, actual=exc.actual)) from exc
            finally:
                s.close()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    # get: latest artifact of the kind via session heads. Resolve heads once (either channel),
    # check the kind once, then fetch the payload over the matching channel.
    store = None if daemon else _store()
    heads = (_http(daemon, "GET", f"/api/sessions/{ns.session}")["heads"] if daemon
             else store.get_session(ns.session)["heads"])
    h = heads.get(ns.kind)
    if not h:
        raise SystemExit(i18n.t("no_artifact", kind=ns.kind))
    if daemon:
        req = urllib.request.Request(
            f"http://127.0.0.1:{daemon['port']}/api/artifacts/{h}",
            headers={"X-PCUI-Secret": daemon["secret"]})
        with urllib.request.urlopen(req, timeout=30) as resp:
            sys.stdout.write(resp.read().decode("utf-8"))
    else:
        sys.stdout.write(store.get_artifact(h)["payload"].decode("utf-8"))
    return 0


# Convenience flags for op fields team.py reads as FILES. The CLI inlines the file CONTENT
# (the daemon refuses client-supplied paths, §4.3); the authoritative op/field whitelist
# lives in server.py — anything else rides --args and is validated there.
_RUN_FILE_FIELDS = ("file", "context", "slate", "frame_output", "slate_output", "audit_receipt")


def cmd_run(ns) -> int:
    daemon = _daemon()
    if daemon is None:
        raise SystemExit(i18n.t("run_need_daemon"))
    body: dict = {}
    if ns.session is not None:
        body["session"] = _session_id(ns.session or None)
    op: dict = {"op": ns.op}
    if ns.args:
        extra = json.loads(ns.args)
        if not isinstance(extra, dict):
            raise SystemExit(i18n.t("args_object"))
        op.update(extra)
    for field in _RUN_FILE_FIELDS:
        path = getattr(ns, field)
        if path:
            op[field] = json.loads(Path(path).read_text(encoding="utf-8"))
    if ns.out:
        from .operator_output import full_result_args
        op = full_result_args(op["op"], op)
    out = _http(daemon, "POST", "/api/team/session", {**body, "ops": [op]}, timeout=630)
    result = out[0] if isinstance(out, list) and len(out) == 1 else out
    ok = (isinstance(result, dict) and result.get("rc") == 0
          and result.get("error") is None and result.get("result") is not None)
    if ns.out:
        from .operator_output import save_result
        try:
            summary = save_result(result, ns.out)
        except OSError as exc:
            print(json.dumps({"error": "output_save_failed", "message": str(exc),
                              "operatorCompleted": True, "row": result}, ensure_ascii=False), file=sys.stderr)
            return 1
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _companion_dest(ns) -> Path:
    base = Path(ns.dir) if ns.dir else agent_skill_dir(ns.target)
    return base / COMPANION_NAME


def cmd_install_agent(ns) -> int:
    dest = _companion_dest(ns)
    text = render_companion()
    print(i18n.t("companion_dest", dest=dest / "SKILL.md"))
    if not ns.yes and input(i18n.t("write_confirm")).strip() != "yes":
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(text, encoding="utf-8")
    print(i18n.t("installed"))
    return 0


def cmd_uninstall_agent(ns) -> int:
    dest = _companion_dest(ns)
    marker = dest / "SKILL.md"
    # Refuse to delete anything that is not OUR skill: the target dir must carry the
    # companion's own name in its frontmatter. Protects against a mistyped --dir.
    if not marker.is_file() or f"name: {COMPANION_NAME}" not in marker.read_text(encoding="utf-8"):
        raise SystemExit(i18n.t("refuse_delete", dest=dest, name=COMPANION_NAME))
    shutil.rmtree(dest)
    print(i18n.t("removed", dest=dest))
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # Select the display language before constructing argparse so --help itself is
    # localized. Keep command names and machine-readable JSON language-invariant.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=i18n.LANGS)
    pre.add_argument("--env-file")
    selected, _ = pre.parse_known_args(raw_argv)
    i18n.set_lang(selected.lang)
    argparse._ = i18n.argparse_text  # type: ignore[attr-defined]

    ap = argparse.ArgumentParser(prog="pcui", description=i18n.t("description"))
    ap.add_argument("--lang", choices=i18n.LANGS, help=i18n.t("lang_help"))
    # Read by the pre-parser above; declared here so `pcui --env-file X serve` parses.
    ap.add_argument("--env-file", dest="global_env_file", metavar="PATH",
                    help=i18n.t("env_file_help"))
    try:
        config_path = Path(selected.env_file).expanduser() if selected.env_file else (
            data_dir() / CONFIG_ENV_NAME)
        runtime_config = load_runtime_config(config_path)
    except ValueError as exc:
        ap.error(str(exc))
    sub = ap.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help=i18n.t("serve_help"))
    serve.add_argument("--port", type=int, default=runtime_config.local_port)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--workspace", default=None, help=i18n.t("workspace_help"))
    serve.set_defaults(workspace_config=str(config_path))
    # The dev-checkout default for --dist/--projection. It follows the repo layout, not SKILLS_ROOT,
    # which PCUI_SKILLS_ROOT may point outside the checkout entirely.
    _web = PROJECT_ROOT / "frontend" / "web"
    serve.add_argument("--dist", default=str(_web / "dist") if (_web / "dist").is_dir() else None,
                       help=i18n.t("dist_help"))
    serve.add_argument("--projection",
                       default=str(_web / "public/projection")
                       if (_web / "public/projection").is_dir() else None,
                       help=i18n.t("projection_help"))
    serve.set_defaults(fn=cmd_serve)

    online = sub.add_parser("online-serve", help=i18n.t("online_help"))
    online.add_argument("--port", type=int, default=runtime_config.online_port)
    online.add_argument("--host", default="127.0.0.1")
    online.add_argument("--provider",
                        choices=["opencode", "deepseek", "openai-compatible", "echo", "none"],
                        default=runtime_config.llm_provider
                        if runtime_config.online_use_api_key else "none",
                        help=i18n.t("provider_help"))
    online.add_argument("--env-file", default=str(config_path),
                        help=i18n.t("env_file_help"))
    online.add_argument("--qa-daily-limit", type=int, default=15,
                        help=i18n.t("qa_limit_help"))
    online.add_argument("--qa-token-budget", type=int, default=1_500_000,
                        help=i18n.t("qa_budget_help"))
    # Host-shaped, so it belongs on the command line. The default tracks core count because
    # each in-flight request saturates about one core: measured on the 2-vCPU host, throughput
    # PEAKS at concurrency 2 and then falls (14.2 -> 13.6 -> 12.7 per minute at 2/3/4) while
    # latency grows proportionally. Overshooting costs throughput AND latency, so this is one
    # of the few places a cpu_count-derived default is the correct answer rather than a guess
    # (design §9.1). Memory is the other bound at ~210 MB per in-flight request.
    online.add_argument("--deterministic-workers", type=int,
                        default=max(2, min(8, os.cpu_count() or 2)),
                        help="thread lane width for diagnose/matchup/tune; throughput peaks at "
                             "about one per core (~210 MB per in-flight request)")
    online.add_argument("--worker-idle", type=float, default=600.0,
                        help=i18n.t("idle_help"))
    online.add_argument("--dist", default=str(_web / "dist") if (_web / "dist").is_dir() else None,
                        help=i18n.t("dist_help"))
    online.add_argument("--projection",
                        default=str(_web / "public/projection")
                        if (_web / "public/projection").is_dir() else None,
                        help=i18n.t("projection_help"))
    online.set_defaults(local_online_unlimited=runtime_config.local_online_unlimited)
    online.set_defaults(fn=cmd_online_serve)

    doctor = sub.add_parser("doctor", help=i18n.t("doctor_help"))
    doctor.set_defaults(fn=cmd_doctor)

    mcp = sub.add_parser("mcp", help=i18n.t("mcp_help"))
    mcp.add_argument("--print-config", action="store_true", help=i18n.t("mcp_config_help"))
    mcp.set_defaults(fn=cmd_mcp)

    open_ = sub.add_parser("open", help=i18n.t("open_help"))
    open_.add_argument("--session", help=i18n.t("open_session_help"))
    open_.add_argument("--no-browser", action="store_true", help=i18n.t("no_browser_help"))
    open_.set_defaults(fn=cmd_open)

    session = sub.add_parser("session", help=i18n.t("session_help"))
    session.add_argument("action", choices=["create", "list", "show", "use", "wait"])
    session.add_argument("session_id", nargs="?", help=i18n.t("session_id_help"))
    session.add_argument("--id")
    session.add_argument("--intent", help=i18n.t("intent_help"))
    session.add_argument("--format", choices=["single", "double"])
    session.add_argument("--kind", choices=["build", "rework", "diagnose", "tune"],
                         help=i18n.t("kind_help"))
    session.add_argument("--team", help=i18n.t("team_help"))
    session.add_argument("--team-name", help=i18n.t("team_name_help"))
    session.add_argument("--for", dest="wait_for", default="decision,intake-answer", help=i18n.t("wait_for_help"))
    session.add_argument("--timeout", type=float, default=1800, help=i18n.t("wait_timeout_help"))
    session.set_defaults(fn=cmd_session)

    artifact = sub.add_parser("artifact", help=i18n.t("artifact_help"))
    artifact.add_argument("action", choices=["get", "put"])
    artifact.add_argument("--session", help=i18n.t("artifact_session_help"))
    artifact.add_argument("--kind", required=True)
    artifact.add_argument("--file")
    artifact.add_argument("--revision", type=int, help=i18n.t("artifact_revision_help"))
    artifact.set_defaults(fn=cmd_artifact)

    run = sub.add_parser("run", help=i18n.t("run_help"))
    run.add_argument("op", help=i18n.t("op_help"))
    run.add_argument("--args", help=i18n.t("args_help"))
    run.add_argument("--out", help=i18n.t("out_help"))
    run.add_argument("--session", nargs="?", const="", default=None, help=i18n.t("run_session_help"))
    for f in _RUN_FILE_FIELDS:
        run.add_argument(f"--{f.replace('_', '-')}", dest=f,
                         help=i18n.t("file_help", field=f))
    run.set_defaults(fn=cmd_run)

    for name, fn in (("install-agent", cmd_install_agent),
                     ("uninstall-agent", cmd_uninstall_agent)):
        p = sub.add_parser(
            name,
            help=i18n.t("install_help" if name == "install-agent" else "uninstall_help"),
        )
        p.add_argument("--target", choices=TARGETS, default="claude")
        p.add_argument("--dir", help=i18n.t("dir_help"))
        if name == "install-agent":
            p.add_argument("--yes", action="store_true", help=i18n.t("yes_help"))
        p.set_defaults(fn=fn)

    ns = ap.parse_args(raw_argv)
    return ns.fn(ns)


if __name__ == "__main__":
    raise SystemExit(main())
