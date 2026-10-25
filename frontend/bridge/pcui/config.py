"""One per-user env file for runtime defaults and online-only secrets."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import data_dir


CONFIG_ENV_NAME = "pc.env"
_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class EnvFileError(ValueError):
    def __init__(self, path: Path, line: int):
        super().__init__(f"invalid env file entry: {path}:{line}")
        self.path = path
        self.line = line


@dataclass(frozen=True)
class RuntimeConfig:
    local_port: int = 1025
    online_port: int = 1026
    online_use_api_key: bool = True
    local_online_unlimited: bool = False
    llm_provider: str = "opencode"
    workspace_dir: str | None = None


def env_file_values(path: Path) -> dict[str, str]:
    """Parse systemd-EnvironmentFile-style ``KEY=value`` without executing or exporting it."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise EnvFileError(path, line_no)
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.fullmatch(key):
            raise EnvFileError(path, line_no)
        if value[:1] in {"'", '"'}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise EnvFileError(path, line_no)
            value = value[1:-1]
        values[key] = value
    return values


def _port(values: dict[str, str], key: str, default: int) -> int:
    raw = values.get(key, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer from 1 to 65535") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"{key} must be an integer from 1 to 65535")
    return value


def _bool(values: dict[str, str], key: str, default: bool) -> bool:
    raw = values.get(key, "true" if default else "false").lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{key} must be true or false")
    return raw == "true"


def load_runtime_config(path: Path | None = None) -> RuntimeConfig:
    """Read non-secret startup defaults without exporting any values into the process."""
    source = path or data_dir() / CONFIG_ENV_NAME
    values = env_file_values(source)
    provider = values.get("PCUI_LLM_PROVIDER", "opencode").strip().lower()
    if provider not in {"opencode", "deepseek", "openai-compatible", "echo", "none"}:
        raise ValueError(
            "PCUI_LLM_PROVIDER must be opencode, deepseek, openai-compatible, echo, or none")
    return RuntimeConfig(
        local_port=_port(values, "PCUI_LOCAL_PORT", 1025),
        online_port=_port(values, "PCUI_ONLINE_PORT", 1026),
        online_use_api_key=_bool(values, "PCUI_ONLINE_USE_API_KEY", True),
        local_online_unlimited=_bool(values, "PCUI_LOCAL_ONLINE_UNLIMITED", False),
        llm_provider=provider,
        workspace_dir=values.get("PCUI_WORKSPACE_DIR") or None,
    )
