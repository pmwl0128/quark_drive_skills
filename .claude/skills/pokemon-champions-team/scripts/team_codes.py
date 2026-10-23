"""Public team codes: one shared, bounded transport for CLI, importers and bridge.

State is private host state, never shipped skill data. No user/game session material
is cached: only canonical public team facts, budgets and a global circuit breaker.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from typing import Callable

SCHEMA = "team-code.v1"
MAP_PATH = Path(__file__).resolve().parents[1] / "data/team-code/entity-map.v18.json"
DEFAULTS = {"INTERVAL_SECONDS": 10, "HOURLY_LIMIT": 60, "DAILY_LIMIT": 300,
            "CACHE_SECONDS": 86400, "BATCH_LIMIT": 20}
IDENTITY_KEYS = {"PCUI_GAME_IDENTITY_POOL", "PCUI_GAME_IDENTITY_STATE_DIR"}
ERROR_MESSAGES = {
    "bad_input": "A public team code must contain ten letters or digits.",
    "not_configured": "Team-code lookup is not configured on this host.",
    "dependency_unavailable": "Team-code network dependencies are unavailable.",
    "cache_miss": "This code has no fresh cached result.",
    "busy": "Team-code lookup is busy; try again later.",
    "rate_limited": "Team-code lookup is temporarily unavailable; try again later.",
    "circuit_open": "Team-code lookup is temporarily suspended.",
    "not_found": "The public team code does not exist or has expired.",
    "maintenance": "The official service is under maintenance.",
    "authentication_failed": "The query identity requires operator attention.",
    "tutorial_required": "The query identity has not completed its tutorial.",
    "version_changed": "The game protocol version requires an update.",
    "upstream_rejected": "The official service rejected the query.",
    "network_error": "The official service could not be reached.",
    "invalid_response": "The official response could not be mapped safely.",
    "state_unavailable": "The shared query state is unavailable.",
}


class TeamCodeError(Exception):
    def __init__(self, code: str, *, retry_after: int | None = None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(ERROR_MESSAGES[code])

    def result(self):
        detail = {"schema": SCHEMA}
        if self.retry_after is not None:
            detail["retry_after_seconds"] = max(1, self.retry_after)
        return {"ok": False, "query": "team-code", "error": {
            "code": self.code, "message": str(self), "details": detail}}


def normalize_code(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise TeamCodeError("bad_input")
    code = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", code):
        raise TeamCodeError("bad_input")
    return code


def explicit_codes(text: str) -> list[str]:
    """Literal public codes only; never infer/OCR-repair/enumerate identifiers."""
    out = []
    for match in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z0-9]{10}(?![A-Za-z0-9])", text or ""):
        token = match.group()
        labeled = re.search(r"(?:队伍码|隊伍碼|コード|\bcode)\s*[:：#]?\s*$",
                            text[max(0, match.start() - 24):match.start()], re.I)
        if any(char.isdigit() for char in token) or token.isupper() or labeled:
            out.append(token.upper())
    return list(dict.fromkeys(out))


def settings() -> dict[str, str]:
    # Only this namespace is read; never export the entire private env to children.
    values = {}
    path = Path.home() / ".pokemon-champions/pc.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and (key.strip().startswith("PCUI_TEAMCODE_") or key.strip() in IDENTITY_KEYS):
                values[key.strip()] = value.strip().strip("\"'")
    values.update({key: value for key, value in os.environ.items()
                   if key.startswith("PCUI_TEAMCODE_") or key in IDENTITY_KEYS})
    if values.get("PCUI_GAME_IDENTITY_POOL"):
        from game_identities import pool_identities
        try:
            pool_identities(values["PCUI_GAME_IDENTITY_POOL"],"team_code")
        except ValueError:
            raise TeamCodeError("not_configured") from None
    return values


def _number(cfg: dict, key: str) -> int:
    try:
        value = int(cfg.get("PCUI_TEAMCODE_" + key, DEFAULTS[key]))
        if value < 1:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise TeamCodeError("not_configured") from None


def state_path(cfg: dict) -> Path:
    return Path(cfg.get("PCUI_TEAMCODE_STATE_DIR") or Path.home() / ".pokemon-champions/team-code") / "state.sqlite3"


def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5, isolation_level=None)
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS cache(code TEXT PRIMARY KEY, at REAL NOT NULL, until REAL NOT NULL,
                                         payload TEXT, error TEXT, map_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guard(id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL DEFAULT 0,
            held_until REAL NOT NULL DEFAULT 0, held_code TEXT, owner TEXT, blocked_until REAL NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO guard(id) VALUES(1);
        CREATE TABLE IF NOT EXISTS attempts(at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_at ON attempts(at);
    """)
    return con


@contextmanager
def _transaction(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _map_payload(payload: dict, mapping: dict) -> dict:
    import dexlink
    try:
        tng = payload["tng"]
        members = tng["mem"]
        if not isinstance(members, list) or len(members) != 6:
            raise ValueError
        items = {}
        for row in tng.get("itms", []):
            if type(row["idx"]) is not int or not 0 <= row["idx"] < 6 or row["idx"] in items:
                raise ValueError
            items[row["idx"]] = row["i"]
            if type(row["i"]) is not int or row["i"] < 0:
                raise ValueError
        raw_members = []
        for index, member in enumerate(members):
            if type(member["b8"]) is not int or not 0 <= member["b8"] < len(mapping["natures"]):
                raise ValueError
            spread = dict(zip(["hp", "atk", "def", "spa", "spd", "spe"],
                              [member[k] for k in ["b9", "ba", "bb", "bd", "be", "bc"]]))
            if any(type(v) is not int or not 0 <= v <= 32 for v in spread.values()) or sum(spread.values()) > 66:
                raise ValueError
            move_ids = member["bf"]
            if (not isinstance(move_ids, list) or not 1 <= len(move_ids) <= 4
                    or any(type(v) is not int or v < 0 for v in move_ids)):
                raise ValueError
            moves = [mapping["moves"][str(v)] for v in move_ids if v > 0]
            if not 1 <= len(moves) <= 4 or len(set(moves)) != len(moves):
                raise ValueError
            raw_members.append({"species": mapping["species"][f'{member["b0"]}:{member["b1"]}'],
                "ability": mapping["abilities"][str(member["b5"])],
                "item": mapping["items"][str(items[index])] if items.get(index, 0) > 0 else None,
                "nature": mapping["natures"][member["b8"]], "moves": moves, "spread": spread,
                "tera": None, "completeness": "observed_full_set"})
        for field, kind in [("species", "pokemon"), ("ability", "ability"), ("item", "item"),
                            ("nature", "nature"), ("moves", "move")]:
            names = sorted({name for m in raw_members for name in (m[field] if field == "moves" else [m[field]]) if name})
            resolved = dexlink.resolve_names(names, kind, fuzzy=False)
            if any(not resolved.get(n, {}).get("ok") for n in names):
                raise ValueError
            for member in raw_members:
                if field == "moves":
                    member[field] = [resolved[n]["canonical"] for n in member[field]]
                elif member[field]:
                    member[field] = resolved[member[field]]["canonical"]
        return {"schema_version": 1, "format": None, "season": None, "rule": None,
                "pokemon": raw_members, "provenance": None}
    except (KeyError, TypeError, ValueError, IndexError, dexlink.DexUnavailable):
        raise TeamCodeError("invalid_response") from None


def _official(code: str, cfg: dict, *, before_request=None) -> dict:
    try:
        from team_code_protocol import OfficialClient, ProtocolError
    except ImportError:
        raise TeamCodeError("dependency_unavailable") from None
    identity = cfg.get("PCUI_TEAMCODE_GUEST_UUID", "")
    if not re.fullmatch(r"[A-Za-z0-9]{42}", identity):
        raise TeamCodeError("not_configured")
    version = cfg.get("PCUI_TEAMCODE_CLIENT_VERSION", "1.2.2")
    if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){1,3}", version):
        raise TeamCodeError("not_configured")
    client = OfficialClient(identity, version, before_request=before_request)
    try:
        payload = client.resolve(code)
        return _map_payload(payload, json.loads(MAP_PATH.read_text(encoding="utf-8")))
    except ProtocolError as exc:
        if exc.http == 429:
            raise TeamCodeError("rate_limited", retry_after=3600) from None
        if exc.http in {401, 403}:
            raise TeamCodeError("authentication_failed") from None
        known = {31502: "not_found", 9990: "maintenance", 1102: "authentication_failed", 10100: "authentication_failed",
                 11200: "authentication_failed", 15002: "tutorial_required", 9901: "version_changed"}
        raise TeamCodeError(known.get(exc.code, "upstream_rejected")) from None
    except TeamCodeError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise TeamCodeError("invalid_response") from None
    except Exception:
        # Requests exceptions may contain URLs, response data or request headers.
        raise TeamCodeError("network_error") from None
    finally:
        client.session.close()


def _gateway_url(cfg: dict) -> str | None:
    value = cfg.get("PCUI_TEAMCODE_GATEWAY_URL")
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
                 and parsed.port in (None, 443) and parsed.path == "/api/team/code"
                 and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise TeamCodeError("not_configured")
    return value


def _gateway(code: str, cfg: dict, now: float) -> tuple[dict, float]:
    """Use one operator-controlled host for the identity and its shared official budget."""
    import requests
    headers = {"Accept": "application/json"}
    if cfg.get("PCUI_TEAMCODE_GATEWAY_KEY"):
        headers["x-pcui-smoke-key"] = cfg["PCUI_TEAMCODE_GATEWAY_KEY"]
    try:
        deadline = time.monotonic() + 80
        with requests.post(_gateway_url(cfg), json={"code": code, "format": "single"},
                           headers=headers, timeout=(10, 65), stream=True,
                           allow_redirects=False) as response:
            chunks, size = [], 0
            # A full buffered chunk can take unbounded time on a slow-drip response.
            for chunk in response.iter_content(1):
                size += len(chunk)
                if size > 65536 or time.monotonic() > deadline:
                    raise TeamCodeError("invalid_response")
                chunks.append(chunk)
            data = json.loads(b"".join(chunks))
            if response.status_code != 200:
                detail = data.get("detail", data)
                error = detail.get("error", {}).get("code") if isinstance(detail, dict) else None
                raise TeamCodeError(error if error in ERROR_MESSAGES else "upstream_rejected")
            if data.get("schema") != SCHEMA or data.get("code") != code:
                raise TeamCodeError("invalid_response")
            fetched = datetime.fromisoformat(data["cache"]["fetched_at"].replace("Z", "+00:00"))
            at = fetched.timestamp()
            if fetched.tzinfo is None or at > now + 300 or at + _number(cfg, "CACHE_SECONDS") <= now:
                raise TeamCodeError("invalid_response")
            # The Web endpoint requires a caller-selected format; official codes do not encode it.
            team = {**data["team"], "format": None, "season": None, "rule": None}
            return team, min(at, now)
    except TeamCodeError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise TeamCodeError("invalid_response") from None
    except requests.RequestException:
        raise TeamCodeError("network_error") from None


def lookup(value: str, *, network: bool = True, scenario: str = "interactive",
           config: dict | None = None, fetch: Callable | None = None, clock=time.time) -> dict:
    """One explicit code. All callers on a host share one persisted budget and lease.

    ``network=False`` never makes an official request. Injection is for hermetic tests;
    HTTP/LLM callers must not expose config, fetch or a state-path override.
    """
    code = normalize_code(value)
    if scenario not in {"interactive", "pipeline"}:
        raise TeamCodeError("bad_input")
    cfg = settings() if config is None else config
    map_hash = hashlib.sha256(MAP_PATH.read_bytes()).hexdigest()
    con = pool = lease = None
    def check_identity():
        if lease:
            try:
                lease.check()
            except identity_module.PoolError as exc:
                raise TeamCodeError("circuit_open" if exc.code=="circuit_open" else "busy",
                                    retry_after=exc.retry_after) from None
    try:
        con = _connect(state_path(cfg))
        now = clock()
        owner = secrets.token_hex(12)
        with _transaction(con):
            row = con.execute("SELECT at,until,payload,error,map_hash FROM cache WHERE code=?", (code,)).fetchone()
            if row and row[1] > now and row[4] == map_hash:
                if row[3]:
                    raise TeamCodeError(row[3])
                return {"schema": SCHEMA, "code": code, "team": json.loads(row[2]),
                        "cache": {"hit": True, "fetched_at": _iso(row[0])}}
            if not network:
                raise TeamCodeError("cache_miss")
            if fetch is None:
                if (not _gateway_url(cfg) and not cfg.get("PCUI_GAME_IDENTITY_POOL")
                        and not re.fullmatch(r"[A-Za-z0-9]{42}", cfg.get("PCUI_TEAMCODE_GUEST_UUID", ""))):
                    raise TeamCodeError("not_configured")
                if not all(importlib.util.find_spec(name) for name in ("requests", "cryptography")):
                    raise TeamCodeError("dependency_unavailable")
            next_at, held, _held_code, blocked = con.execute(
                "SELECT next_at,held_until,held_code,blocked_until FROM guard WHERE id=1").fetchone()
            if blocked > now:
                raise TeamCodeError("circuit_open", retry_after=int(blocked - now) + 1)
            if held > now:
                raise TeamCodeError("busy", retry_after=5)
            if next_at > now:
                raise TeamCodeError("rate_limited", retry_after=int(next_at - now) + 1)
            con.execute("DELETE FROM attempts WHERE at<=?", (now - 86400,))
            hour, day = con.execute("SELECT SUM(at>?),COUNT(*) FROM attempts", (now - 3600,)).fetchone()
            if (hour or 0) >= _number(cfg, "HOURLY_LIMIT") or day >= _number(cfg, "DAILY_LIMIT"):
                raise TeamCodeError("rate_limited", retry_after=3600)
            if cfg.get("PCUI_GAME_IDENTITY_POOL") and not _gateway_url(cfg):
                import game_identities as identity_module
                try:
                    pool = identity_module.Pool(cfg["PCUI_GAME_IDENTITY_POOL"],
                        identity_module.state_directory(cfg), clock=clock)
                    lease = pool.acquire("team_code",180)
                except ValueError:
                    raise TeamCodeError("not_configured") from None
                except identity_module.PoolError as exc:
                    raise TeamCodeError(exc.code if exc.code in ERROR_MESSAGES else "state_unavailable",
                                        retry_after=exc.retry_after) from None
                cfg = {**cfg,"PCUI_TEAMCODE_GUEST_UUID":lease.identity['uuid']}
            con.execute("INSERT INTO attempts(at) VALUES(?)", (now,))
            con.execute("UPDATE guard SET next_at=?,held_until=?,held_code=?,owner=? WHERE id=1",
                        (now + _number(cfg, "INTERVAL_SECONDS"), now + 180, code, owner))
            con.execute("DELETE FROM cache WHERE until<?", (now - 7 * 86400,))
        try:
            if fetch is None and _gateway_url(cfg):
                team, result_at = _gateway(code, cfg, clock())
            else:
                team = fetch(code,cfg) if fetch else _official(code,cfg,before_request=check_identity)
                result_at = clock()
            payload = json.dumps(team, ensure_ascii=False, separators=(",", ":"))
            if len(payload) > 65536 or not isinstance(team, dict) or len(team.get("pokemon", [])) != 6:
                raise TeamCodeError("invalid_response")
            with _transaction(con):
                con.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)", (
                    code, result_at, result_at + _number(cfg, "CACHE_SECONDS"), payload, None, map_hash))
            return {"schema": SCHEMA, "code": code, "team": team,
                    "cache": {"hit": False, "fetched_at": _iso(result_at)}}
        except TeamCodeError as exc:
            if lease and exc.code not in {"not_found","busy","circuit_open","not_configured","dependency_unavailable"}:
                delay = {"network_error":60,"maintenance":900,"rate_limited":3600}.get(exc.code,86400)
                lease.fail(exc.code,retry_after=max(delay,exc.retry_after or 0),
                           permanent=exc.code in {"authentication_failed","tutorial_required","version_changed","upstream_rejected","invalid_response"})
            with _transaction(con):
                if exc.code == "not_found":
                    con.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)",
                                (code, clock(), clock() + 3600, None, exc.code, map_hash))
                elif exc.code not in {"busy","circuit_open"} and not (
                        exc.code == "rate_limited" and fetch is None and _gateway_url(cfg)):
                    # The gateway owns official refusal backoff. Its 429 can also
                    # mean visitor/burst throttling; do not invent an hour-long
                    # official-service circuit on the forwarding workstation.
                    delay = {"authentication_failed": 86400, "tutorial_required": 86400,
                             "version_changed": 86400, "rate_limited": 3600, "maintenance": 900,
                             "network_error": 60}.get(exc.code, 900)
                    con.execute("UPDATE guard SET blocked_until=MAX(blocked_until,?) WHERE id=1", (clock() + delay,))
            raise
        except Exception:
            with _transaction(con):
                con.execute("UPDATE guard SET blocked_until=MAX(blocked_until,?) WHERE id=1", (clock() + 900,))
            raise TeamCodeError("invalid_response") from None
        finally:
            con.execute("UPDATE guard SET held_until=0,held_code=NULL,owner=NULL WHERE id=1 AND owner=?", (owner,))
    except (sqlite3.Error, OSError, json.JSONDecodeError):
        raise TeamCodeError("state_unavailable") from None
    finally:
        try:
            if lease:
                lease.release()
        finally:
            if pool:
                pool.close()
            if con:
                con.close()


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def status() -> dict:
    cfg = settings()
    path = state_path(cfg)
    out = {"schema": SCHEMA, "configured": bool(_gateway_url(cfg) or cfg.get("PCUI_GAME_IDENTITY_POOL")
           or re.fullmatch(r"[A-Za-z0-9]{42}", cfg.get("PCUI_TEAMCODE_GUEST_UUID", ""))),
           "dependencies_available": all(importlib.util.find_spec(name) is not None for name in ("requests", "cryptography")),
           "limits": {key.lower(): _number(cfg, key) for key in DEFAULTS}, "circuit_open": False}
    if path.exists():
        with sqlite3.connect(path) as con:
            row = con.execute("SELECT blocked_until FROM guard WHERE id=1").fetchone()
            out["circuit_open"] = bool(row and row[0] > time.time())
    return out
