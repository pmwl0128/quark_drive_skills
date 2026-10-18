"""How an AI assistant reaches this bridge: the pcui command line it is given and the companion skill
that teaches it the session workflow (frontend/design.md §5). `install-agent`, `doctor` and the
Console's setup card share these so they never disagree about what "installed" means."""
from __future__ import annotations

import shutil
import os
import shlex
import sys
from pathlib import Path

COMPANION_NAME = "pokemon-champions-ui-session"
TARGETS = ("claude", "codex")
_TEMPLATE = Path(__file__).parent / "companion" / "SKILL.md"


def pcui_argv() -> list[str]:
    """A pcui invocation that works from any working directory and in any shell. Without an installed
    `pcui` script, run the package folder itself (`pcui/__main__.py` puts the bridge on sys.path)."""
    installed = shutil.which("pcui")
    if installed:
        return [Path(installed).as_posix()]
    return [Path(sys.executable).as_posix(), Path(__file__).resolve().parent.as_posix()]


def shell_argv(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    return shlex.join(argv)


def pcui_command() -> str:
    return ("& " if os.name == "nt" else "") + shell_argv(pcui_argv())


def agent_skill_dir(target: str) -> Path:
    return Path.home() / f".{target}" / "skills"


def render_companion() -> str:
    text = _TEMPLATE.read_text(encoding="utf-8")
    return (text.replace("{{PCUI_CMD}}", pcui_command())
                .replace("{{BRIDGE_DIR}}", Path(__file__).resolve().parents[1].as_posix()))


def companion_status(base: Path) -> str:
    """installed: the file matches what install-agent would write now; outdated: ours, but older."""
    skill = base / COMPANION_NAME / "SKILL.md"
    try:
        current = skill.read_text(encoding="utf-8")
    except OSError:
        return "missing"
    if f"name: {COMPANION_NAME}" not in current:
        return "missing"
    return "installed" if current == render_companion() else "outdated"


def companion_report() -> list[dict[str, str]]:
    return [{"target": target, "status": companion_status(agent_skill_dir(target))} for target in TARGETS]
