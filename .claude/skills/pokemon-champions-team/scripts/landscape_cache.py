"""Disposable, fingerprint-bound structural facts; never an authority or a source-data writer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

import handover
from team_output import write_json

SCRIPTS = Path(__file__).resolve().parent
SKILLS = SCRIPTS.parent.parent
MAX_BYTES = 8 * 1024 * 1024
SCHEMA = 1


class AuthorityChanged(RuntimeError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _files(paths: list[Path]) -> list[tuple[str, str | None]]:
    rows = []
    for path in sorted(set(paths)):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            digest = None
        rows.append((str(path.resolve()), digest))
    return rows


def _code() -> list[tuple[str, str | None]]:
    return _files(list(SCRIPTS.glob("*.py")) + list((SKILLS / "pokemon-champions-dex/scripts").rglob("*.py")))


_LOADED_CODE = _code()
_AUTHORITIES = [SKILLS / "pokemon-champions-meta/data/current.json",
                SKILLS / "pokemon-champions-dex/data/champions_dex.json",
                SKILLS / "pokemon-champions-dex/data/champions_dex.sqlite"]
_LOADED_AUTHORITIES = _files(_AUTHORITIES)


def directory() -> Path:
    override = os.environ.get("CHAMP_LANDSCAPE_CACHE")
    if override:
        return Path(override)
    project = SKILLS.parent.parent
    if (project / "AGENTS.md").is_file() and (project / "dev").is_dir():
        return project / ".cache" / "team-landscape"
    return Path.home() / ".pokemon-champions" / "cache" / "landscape"


def signature(team_dir: Path, fmt: str, rule: str) -> dict[str, Any]:
    code = _code()
    if code != _LOADED_CODE:
        raise AuthorityChanged("landscape implementation changed in this process; restart the CLI/session")
    if _files(_AUTHORITIES) != _LOADED_AUTHORITIES:
        raise AuthorityChanged("landscape dex/current authority changed in this process; restart the CLI/session")
    files = [*team_dir.glob(f"*_{fmt}.jsonl"), team_dir / "index.json", handover.receipt_path(team_dir),
             handover.dex_path(team_dir), *_AUTHORITIES]
    try:
        token = handover.pool_token(team_dir, fmt, rule)
    except handover.HandoverError as exc:
        raise AuthorityChanged(f"landscape handover authority is invalid: {exc}") from exc
    return {"code": code, "data": _files(files), "pool_token": token}


def evaluate(teams: list[dict], options: dict, compute: Callable[[], dict], *,
             authority: Callable[[], Any], enabled: bool = True, expected: Any = None) -> tuple[dict, dict]:
    before = authority()
    if expected is not None and before != expected:
        raise AuthorityChanged("landscape sources changed before calculation; rerun the query")
    key = _digest({"schema": SCHEMA, "teams": teams, "options": options, "authority": before})
    path = directory() / f"{key}.json"
    status = "miss" if enabled else "disabled"
    if enabled:
        try:
            if path.stat().st_size <= MAX_BYTES:
                cached = json.loads(path.read_text(encoding="utf-8"))
                result = cached.get("result")
                if (cached.get("schema") == SCHEMA and cached.get("fingerprint") == key
                        and isinstance(result, dict) and result.get("kind") == "landscape"
                        and result.get("format") == options.get("format")
                        and cached.get("result_digest") == _digest(result)):
                    if authority() != before:
                        raise AuthorityChanged("landscape sources changed during the cache read; rerun the query")
                    return result, {"status": "hit", "fingerprint": key}
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError):
            pass
        except OSError:
            status = "unavailable"
    result = compute()
    if authority() != before:
        raise AuthorityChanged("landscape sources changed during calculation; rerun the query")
    if enabled:
        payload = {"schema": SCHEMA, "fingerprint": key, "result_digest": _digest(result), "result": result}
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= MAX_BYTES:
            try:
                write_json(str(path), payload)
            except OSError:
                status = "unavailable"
        else:
            status = "not_stored"
    return result, {"status": status, "fingerprint": key}
